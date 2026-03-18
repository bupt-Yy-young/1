# London Gold Monitor

一个可以直接放进 GitHub 仓库运行的小时级监测工具：

- 每小时自动抓取一次伦敦金（以 `XAU/USD` 现货金作为监测标的）
- 计算与上一次采样相比的涨跌额和涨跌幅
- 统计最近 24 小时的最低、最高、均值与振幅
- 到达新整点后，自动通过 SMTP 发送邮件给你
- 把历史样本保存在仓库的 `data/state.json`，下次运行继续对比

## 目录结构

```text
.
├── .github/workflows/monitor.yml
├── data/state.json
├── src/monitor.py
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## 一、创建仓库并上传代码

1. 在 GitHub 新建一个仓库，比如：`london-gold-monitor`
2. 把本项目所有文件上传到仓库根目录
3. 推送到默认分支（通常是 `main`）

## 二、申请数据 API Key

本项目默认使用 **Alpha Vantage** 的黄金现货接口。

你需要申请一个 API Key，然后在 GitHub 仓库里配置成 Secret：

- Secret 名称：`ALPHAVANTAGE_API_KEY`

## 三、配置邮件发送所需 Secrets

进入你的 GitHub 仓库：

`Settings -> Secrets and variables -> Actions -> New repository secret`

至少添加以下 Secrets：

| Secret 名称 | 说明 | 示例 |
|---|---|---|
| `SMTP_HOST` | SMTP 服务器地址 | `smtp.qq.com` |
| `SMTP_PORT` | SMTP 端口 | `587` |
| `SMTP_USERNAME` | SMTP 登录用户名 | `your@qq.com` |
| `SMTP_PASSWORD` | SMTP 密码或授权码 | `your_app_password` |
| `EMAIL_FROM` | 发件人邮箱 | `your@qq.com` |
| `EMAIL_TO` | 收件人邮箱 | `your@qq.com` |
| `EMAIL_SENDER_NAME` | 发件人显示名称 | `London Gold Monitor` |
| `REPORT_TIMEZONE` | 报告展示时区 | `Asia/Shanghai` |

> 如果你用 QQ 邮箱，通常需要先在邮箱后台开启 SMTP，并使用授权码而不是网页登录密码。

## 四、运行方式

### 1）自动运行

工作流已经配置为：

```yaml
on:
  schedule:
    - cron: '2 * * * *'
```

它表示：**每小时第 2 分钟运行一次**。

这样做是为了降低 GitHub Actions 在整点高峰期延迟或丢任务的概率。

### 2）手动运行

你也可以进入仓库的 `Actions` 页面，手动触发 `London Gold Monitor` 工作流测试是否配置成功。

## 五、本地测试

1. 复制环境变量模板：

```bash
cp .env.example .env
```

2. 修改 `.env` 中的值

3. 安装依赖：

```bash
pip install -r requirements.txt
```

4. 本地试跑：

```bash
export $(grep -v '^#' .env | xargs)
python src/monitor.py
```

如果你只想看邮件内容、不真正发信，可以在 `.env` 中设置：

```env
DRY_RUN=1
```

## 六、邮件内容说明

每封邮件会包含：

- 当前现货金价格
- 采样 UTC 时间、上海时间、伦敦时间
- 相比上一次采样的涨跌金额与涨跌幅
- 最近 24 小时最低价 / 最高价 / 均值 / 振幅

## 七、注意事项

1. GitHub Actions 的定时任务使用 **UTC** 时间基准。
2. 如果仓库是公开仓库且 **60 天无活动**，GitHub 可能会自动禁用定时工作流。
3. 本项目将 `data/state.json` 提交回仓库，以保存历史样本。
4. 这是监测提醒工具，不构成投资建议。

## 八、自定义建议

你可以很容易继续扩展：

- 只在涨跌幅超过阈值时发信
- 同时发到多个邮箱
- 接入企业微信 / Telegram / 飞书
- 生成折线图后作为附件发送
- 改成每 15 分钟监测一次

## 九、常见问题

### 1. 为什么不是“整点 00 分”发送？

因为 GitHub Actions 官方说明整点是高峰时段，定时任务可能延迟。把任务放到每小时第 2 分钟更稳定。

### 2. 为什么使用 XAU/USD 现货金？

在自动化监测场景下，`XAU/USD` 是实现“伦敦金”监测最常见、最直接的技术替代口径之一。

### 3. 仓库里为什么会自动出现提交记录？

因为工具会把最新采样写回 `data/state.json`，这样下次运行才能继续计算波动。
