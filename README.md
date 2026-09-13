# Adwatch Shopee 广告优化器

Adwatch 是一个运行在 macOS 本机的 Shopee 广告只读分析工具。它通过紫鸟 CLI
读取 Shopee Ads 页面，把原始观测、活动配置、不可变建议、人工反馈和效果评估
保存到本地 SQLite，并提供 Markdown 日报和仅监听回环地址的管理页面。

工具不会自动修改广告，也不会上传数据或发送通知。当前版本不采集 TikTok，
不读取订单、成本、利润、库存或退款等经营数据。

## 工作方式

每日流程固定为：

```text
获取本机互斥锁
→ 打开或复用指定 Shopee 店铺会话
→ 回填最近 30 日缺口并采集广告数据
→ 检查数据质量
→ 评估已经人工执行的建议
→ 生成新的只读建议
→ 原子写入本地日报和结构化运行日志
```

建议只基于 Shopee 广告事实，支持预算、目标 ROAS、暂停/恢复和结构调整等动作。
每条建议包含当前值、建议值、证据、置信度、风险、观察期、成功条件和回退条件。
建议永远不会直接写回 Shopee。

## 安装

需要 Python 3.11 或更高版本，以及已登录目标店铺的紫鸟客户端和
`ziniao-cli`。

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

至少在 `.env` 中填写真实 Shopee 店铺 ID：

```dotenv
ADWATCH_DATA_DIR=./var
ZINIAO_SHOPEE_STORE_ID=your-store-id
ZINIAO_SHOPEE_STORE_NAME=your-store-name
ADWATCH_TIMEZONE=Asia/Shanghai
ADWATCH_DAILY_RUN_TIME=09:00
```

其余采集、回填、策略和本地 Web 参数见 [.env.example](.env.example)。

## 六个命令

```bash
# 首次建库，回填最多 30 日，并立即生成建议和日报
.venv/bin/adwatch init

# 只采集一个业务日；默认前一日
.venv/bin/adwatch collect --date 2026-09-10

# 执行完整每日流程；date 是报告日，分析前一日数据
.venv/bin/adwatch run daily --date 2026-09-11

# 启动仅监听 127.0.0.1 的本地页面
.venv/bin/adwatch dashboard --port 8765

# 查询、安装或卸载唯一的 09:00 LaunchAgent
.venv/bin/adwatch schedule status
.venv/bin/adwatch schedule install
.venv/bin/adwatch schedule uninstall

# 检查 Python、SQLite、ziniao-cli、Shopee 店铺和本机端口
.venv/bin/adwatch doctor
```

`collect` 和 `run daily` 固定为 Shopee，不接受平台或 mode 参数。同一报告日的
成功每日任务会幂等退出；失败不会覆盖上一版日报，也不会留下部分活动的新建议。

## 本地页面

Dashboard 提供以下只读或人工录入页面：

- `/`：当日数据质量、指标、建议和证据。
- `/campaigns`：活动现状、近期指标和不可变配置版本。
- `/reviews`：建议反馈、最新评估和活动/动作有效率。
- `/health`：不含敏感信息的本机健康状态。

页面只允许 `127.0.0.1`/localhost Host，表单使用短期、一次性且绑定具体对象版本
的 nonce。所有 HTML 内容均转义；页面不加载远端资源。

活动档案可以设置最低 ROAS、扩量强度、单次调整上限、观察期和允许动作。
反馈支持“已执行”“修改后执行”“延后”和“拒绝”；执行反馈绑定建议的物理 revision，
后续评估不会错误归因到同 ID 的其他版本。

## 本地数据

默认文件均位于 `var/`：

```text
var/adwatch.sqlite3
var/reports/shopee-ads-YYYY-MM-DD.md
var/logs/launchd.out.log
var/logs/launchd.err.log
var/backups/launchagent/
```

指标观测和建议版本采用追加式历史记录。重复采集按物理观测身份幂等；报表和网页
按业务日与决策时点读取当时可见的数据，不让未来修订污染历史结果。

## 调度安全

项目只管理 `com.adwatch.daily` 一个 LaunchAgent。plist 固定使用
`Asia/Shanghai`、09:00、`RunAtLoad=true`、当前项目绝对路径和确定性最小 PATH。
安装前会运行 doctor；卸载只接受与当前期望内容精确一致的 plist，并在 bootout
前保存本地备份。

开发和验收不会自动安装调度。只有用户单独确认后才能执行
`adwatch schedule install`。完整步骤见
[macOS 调度手册](docs/operations/adwatch-macos-schedule.md)。

## 开发验证

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python -m ruff check .
git diff --check
```

历史设计、旧经营系统计划和审计资料仍保留在 `docs/` 供追溯，但不代表当前
运行入口或受支持能力。当前文档索引见 [docs/README.md](docs/README.md)。
