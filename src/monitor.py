from __future__ import annotations

import json
import os
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
STATE_PATH = Path("data/state.json")
DEFAULT_TZ = "Asia/Shanghai"
MAX_SAMPLES = 800

# --- 用户定义的推送策略 ---
SUMMARY_HOURS = {10, 14, 18}      # 定时摘要时间（报告时区）
ALERT_ABS_CHANGE = 100.0          # 波动提醒阈值：改为 100 美元/oz
ALERT_PCT_CHANGE = 2.0            # 百分比阈值相应调高
PUSH_HOURS_RANGE = range(8, 23)   # 允许推送的小时区间（08:00 - 22:59）
# -----------------------

class ConfigError(RuntimeError):
    pass

@dataclass
class PriceSample:
    timestamp_utc: str
    price: float
    currency: str
    metal: str
    exchange: str | None = None

def require_env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or not str(value).strip():
        raise ConfigError(f"Missing required environment variable: {name}")
    return str(value).strip()

def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"samples": []}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))

def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def fetch_gold_spot(api_key: str) -> PriceSample:
    params = {
        "function": "GOLD_SILVER_SPOT",
        "symbol": "XAU",
        "apikey": api_key,
    }
    response = requests.get(ALPHA_VANTAGE_URL, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()

    if "Error Message" in payload:
        raise RuntimeError(f"Alpha Vantage error: {payload['Error Message']}")
    if "Note" in payload:
        raise RuntimeError(f"Alpha Vantage note: {payload['Note']}")

    price = payload.get("price") or payload.get("spot_price") or payload.get("value")
    if price is None:
        raise RuntimeError(f"Unexpected Alpha Vantage payload: {payload}")

    ts = payload.get("timestamp")
    timestamp = datetime.now(timezone.utc)
    if ts:
        try:
            timestamp = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass

    return PriceSample(
        timestamp_utc=timestamp.replace(microsecond=0).isoformat(),
        price=float(price),
        currency=str(payload.get("currency", "USD")),
        metal=str(payload.get("metal", "Gold")),
        exchange=payload.get("exchange"),
    )

def last_sample(samples: list[dict[str, Any]]) -> dict[str, Any] | None:
    return samples[-1] if samples else None

def pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return (current - previous) / previous * 100.0

def format_price(value: float) -> str:
    return f"{value:,.2f}"

def format_change(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.2f}"

def format_pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.3f}%"

def parse_iso_utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts).astimezone(timezone.utc)

def summarize_window(samples: list[dict[str, Any]], hours: int = 24) -> dict[str, Any]:
    if not samples:
        return {}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    window = [s for s in samples if parse_iso_utc(s["timestamp_utc"]) >= cutoff]
    if not window:
        window = samples[-1:]

    prices = [float(s["price"]) for s in window]
    minimum = min(prices)
    maximum = max(prices)
    avg = sum(prices) / len(prices)
    span = maximum - minimum
    span_pct = pct_change(maximum, minimum) if minimum else 0.0
    return {
        "count": len(window),
        "min": minimum,
        "max": maximum,
        "avg": avg,
        "span": span,
        "span_pct": span_pct,
        "first_ts": window[0]["timestamp_utc"],
        "last_ts": window[-1]["timestamp_utc"],
    }

def should_send(sample: PriceSample, previous: dict[str, Any] | None, report_tz: str) -> tuple[bool, str | None]:
    now_local = parse_iso_utc(sample.timestamp_utc).astimezone(ZoneInfo(report_tz))

    # 1. 屏蔽周末 (0-4 是周一到周五，5 是周六，6 是周日)
    if now_local.weekday() >= 5:
        return False, "weekend_skip"

    # 2. 屏蔽非推送时段 (例如深夜不推送)
    if now_local.hour not in PUSH_HOURS_RANGE:
        return False, "night_skip"

    # 3. 固定摘要时间推送
    if now_local.hour in SUMMARY_HOURS:
        return True, "scheduled"

    # 4. 波动提醒 (仅在大幅波动时触发)
    if previous:
        prev_price = float(previous["price"])
        delta = abs(sample.price - prev_price)
        delta_pct = abs(pct_change(sample.price, prev_price))
        if delta >= ALERT_ABS_CHANGE or delta_pct >= ALERT_PCT_CHANGE:
            return True, "volatility"

    return False, None

def build_email(
    sample: PriceSample,
    previous: dict[str, Any] | None,
    state: dict[str, Any],
    report_tz: str,
    reason: str,
) -> tuple[str, str]:
    try:
        tz = ZoneInfo(report_tz or DEFAULT_TZ)
    except Exception:
        tz = ZoneInfo(DEFAULT_TZ)

    ts_local = parse_iso_utc(sample.timestamp_utc).astimezone(tz)
    ts_sh = parse_iso_utc(sample.timestamp_utc).astimezone(ZoneInfo("Asia/Shanghai"))
    ts_ldn = parse_iso_utc(sample.timestamp_utc).astimezone(ZoneInfo("Europe/London"))
    samples = state.get("samples", [])
    window = summarize_window(samples, hours=24)

    if previous:
        prev_price = float(previous["price"])
        delta = sample.price - prev_price
        delta_pct = pct_change(sample.price, prev_price)
        previous_line = (
            f"较上一次采样变化: {format_change(delta)} {sample.currency}/oz "
            f"({format_pct(delta_pct)})\n"
            f"上一次采样时间: {previous['timestamp_utc']}"
        )
    else:
        delta = 0.0
        delta_pct = 0.0
        previous_line = "这是首次采样，暂无上一次对比数据。"

    if reason == "scheduled":
        subject_prefix = "[定时摘要]"
        title = "伦敦金定时摘要"
    else:
        subject_prefix = "[波动提醒]"
        title = "伦敦金波动提醒"

    body = f"""{title}

当前价格: {format_price(sample.price)} {sample.currency}/oz
采样时间(报告时区): {ts_local.strftime('%Y-%m-%d %H:%M:%S %Z')}
采样时间(上海): {ts_sh.strftime('%Y-%m-%d %H:%M:%S %Z')}
数据来源字段: metal={sample.metal}, exchange={sample.exchange or 'N/A'}

{previous_line}

最近24小时统计
样本数: {window.get('count', 0)}
最低: {format_price(window.get('min', sample.price))} {sample.currency}/oz
最高: {format_price(window.get('max', sample.price))} {sample.currency}/oz
振幅: {format_change(window.get('span', 0.0))} {sample.currency}/oz ({format_pct(window.get('span_pct', 0.0))})

提醒规则 (已根据需求优化)
1. 推送时段: 周一至周五 {PUSH_HOURS_RANGE.start:02d}:00 - {PUSH_HOURS_RANGE.stop:02d}:00
2. 固定摘要点: {", ".join(f"{h:02d}:00" for h in sorted(SUMMARY_HOURS))}
3. 波动阈值: 单次变化 ≥ {ALERT_ABS_CHANGE:.2f} USD/oz (极大幅度)

说明
1. 非推送时段或周末产生的波动将不会发送邮件。
2. 本工具仅做监测提醒，不构成投资建议。
"""

    subject = (
        f"{subject_prefix} {format_price(sample.price)} {sample.currency}/oz | "
        f"{format_pct(delta_pct if previous else 0.0)}"
    )
    return subject, body

def send_email(subject: str, body: str) -> None:
    smtp_host = require_env("SMTP_HOST")
    smtp_port = int(require_env("SMTP_PORT", "587"))
    smtp_username = require_env("SMTP_USERNAME")
    smtp_password = require_env("SMTP_PASSWORD")
    email_from = require_env("EMAIL_FROM")
    email_to = require_env("EMAIL_TO")
    sender_name = os.getenv("EMAIL_SENDER_NAME", "London Gold Monitor")

    message = MIMEText(body, _charset="utf-8")
    message["Subject"] = subject
    message["From"] = formataddr((sender_name, email_from))
    message["To"] = email_to

    recipients = [item.strip() for item in email_to.split(",") if item.strip()]

    if os.getenv("DRY_RUN", "0") == "1":
        print("[DRY_RUN] Email not sent.")
        print(subject)
        return

    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=60) as server:
            server.login(smtp_username, smtp_password)
            server.sendmail(email_from, recipients, message.as_string())
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=60) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_username, smtp_password)
            server.sendmail(email_from, recipients, message.as_string())

def main() -> None:
    api_key = require_env("ALPHAVANTAGE_API_KEY")
    report_tz = os.getenv("REPORT_TIMEZONE") or DEFAULT_TZ

    state = load_state()
    samples: list[dict[str, Any]] = state.setdefault("samples", [])

    try:
        sample = fetch_gold_spot(api_key)
    except Exception as e:
        print(f"Fetch failed: {e}")
        return

    prev = last_sample(samples)

    # 无论是否推送，都记录数据，保证对比的连续性
    samples.append(
        {
            "timestamp_utc": sample.timestamp_utc,
            "price": sample.price,
            "currency": sample.currency,
            "metal": sample.metal,
            "exchange": sample.exchange,
        }
    )
    if len(samples) > MAX_SAMPLES:
        state["samples"] = samples[-MAX_SAMPLES:]

    should, reason = should_send(sample, prev, report_tz)

    if should and reason:
        subject, body = build_email(sample, prev, state, report_tz, reason)
        send_email(subject, body)
        print(f"Email sent. reason={reason}")
    else:
        print(f"Skip sending. (Reason info: {reason if reason else 'no hit'})")

    save_state(state)
    print("Monitor run completed successfully.")

if __name__ == "__main__":
    main()
