# Adwatch

TikTok 与 Shopee 广告盯盘自动化系统。当前版本提供 SQLite 数据底座、双平台模拟采集、利润与策略分析、风控审批、日报、飞书降级和本地只读看板。

## 环境要求

- macOS
- Python 3.11 或更高版本

## 安装

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

程序直接读取系统环境变量；如需使用 `.env` 文件，可在启动前由 shell 或进程管理器加载。

## 初始化

```bash
export ADWATCH_DATA_DIR="$PWD/var"
python -m adwatch init
python -m adwatch doctor
```

## 运行模拟采集

```bash
python -m adwatch collect --mode mock --date 2026-07-22
```

命令会采集 TikTok 和 Shopee 模拟数据，写入 `var/adwatch.sqlite3`，并生成 `var/reports/quality-2026-07-22.json`。报告包含 `"simulated": true`，用于防止模拟数据被误认为真实业务数据。

同一日期可以安全重跑。系统依据平台、店铺、账户、Campaign、SKU 和日期更新同一条逻辑记录，不会重复累加。

## 紫鸟模式

配置下列环境变量后才能显式使用 `--mode ziniao`：

- `ZINIAO_COMPANY`
- `ZINIAO_USERNAME`
- `ZINIAO_PASSWORD`
- `ZINIAO_ENDPOINT`

缺少配置时命令返回非零状态并说明缺失项。系统不会静默切换到模拟数据。

紫鸟 CLI 应按[紫鸟官方安装页](https://open.ziniao.com/ziniaoCli)完成安装和授权。系统已实现官方 WebDriver 本地 HTTP 控制动作与健康检查；TikTok/Shopee 页面报表选择器及写操作需在真实店铺配置完成后现场核验，未核验前保持禁用。

## 一键每日流程

```bash
python -m adwatch run daily --mode mock --date 2026-07-22
```

该命令依次完成采集、质量校验、经营数据模拟补齐、利润分析、策略建议、日报和飞书投递；未配置飞书时自动保存本地 Markdown。

## 本地看板

```bash
python -m adwatch dashboard --host 127.0.0.1 --port 8765 --date 2026-07-22
```

浏览器打开 `http://127.0.0.1:8765`。看板只读，远程监听必须显式添加 `--allow-remote`。

## 调度配置

```bash
python -m adwatch schedule --print-launchd
```

命令输出每日 08:00 执行所需的 macOS `launchd` 配置，不会自动安装系统服务。

## 开发验证

```bash
python -m pytest -q
python -m ruff check .
```
