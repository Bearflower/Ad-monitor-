# Adwatch

TikTok 与 Shopee 广告盯盘自动化系统。当前版本提供 SQLite 数据底座、双平台模拟采集、数据校验、幂等写入和质量报告。

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

缺少配置时命令返回非零状态并说明缺失项。系统不会静默切换到模拟数据。紫鸟真实传输将在后续真实采集计划中实现。

## 开发验证

```bash
python -m pytest -q
python -m ruff check .
```
