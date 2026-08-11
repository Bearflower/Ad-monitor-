# Adwatch macOS 调度运维手册

## 适用范围

本手册只适用于唯一生产代码源：

```text
/Users/yl/vscode/Cross_Border_Electronic_Commerce
```

生产数据库、报告、状态和日志统一保存在该仓库的 `var/`。旧目录中的代码和
运行数据不参与新任务执行。

## 前置条件

1. Mac 系统时区为 `Asia/Shanghai`。
2. 当前仓库存在可执行文件 `.venv/bin/adwatch`。
3. `.env` 已配置两个紫鸟店铺 ID；需要飞书通知时已配置
   `FEISHU_WEBHOOK`。
4. 紫鸟客户端已登录，ZClaw Bridge 可通过 `ziniao-cli store list` 访问。

生效配置如下：

```dotenv
ADWATCH_DATA_DIR=./var
ADWATCH_DAILY_RUN_TIME=09:00
ADWATCH_TIMEZONE=Asia/Shanghai
ADWATCH_BRIDGE_RETRIES=3
ADWATCH_BRIDGE_RETRY_DELAY_SECONDS=10
ADWATCH_EXCHANGE_RATE_MAX_STALENESS_DAYS=7
```

## 安装

在唯一代码源根目录执行：

```bash
.venv/bin/adwatch schedule install
```

安装会完成以下操作：

1. 校验系统时区和当前仓库运行时。
2. 把期望配置写入 `var/com.adwatch.daily.plist`。
3. 备份已有 Adwatch plist 到 `var/backups/launchagent/`。
4. 识别并注销仍引用旧生产目录的 Adwatch LaunchAgent。
5. 安装
   `~/Library/LaunchAgents/com.adwatch.daily.plist`，显式启用并注册到当前
   GUI 登录域。
6. 验证当前代码路径、工作目录、09:00、`RunAtLoad`、加载和启用状态。

健康安装输出形如：

```text
schedule=installed healthy=true project=/Users/yl/vscode/Cross_Border_Electronic_Commerce time=09:00
```

## 状态与修复

查询：

```bash
.venv/bin/adwatch schedule status
```

健康输出：

```text
schedule=healthy loaded=true enabled=true
```

出现 `plist_missing`、`configuration_drift`、`not_loaded`、`disabled`、
`timezone_mismatch` 或 `legacy_reference` 时执行：

```bash
.venv/bin/adwatch schedule repair
```

健康配置重复安装或修复不会重复执行 `bootstrap`。

查看渲染结果但不修改系统：

```bash
.venv/bin/adwatch schedule --print-launchd
```

卸载前会保存备份；只处理精确标签和精确 plist：

```bash
.venv/bin/adwatch schedule uninstall
```

## 每日执行规则

09:00 运行时：

1. `execution_date` 为北京时间执行日。
2. `report_date` 为执行日前一日。
3. 优先获取执行日 THB/CNY 汇率；没有时使用昨日汇率；仍没有时使用执行日
   前 7 天内最近有效交易日汇率。
4. 把选中汇率应用到 `report_date`，同时保存真实来源日和解析状态。
5. 按配置重试检查紫鸟 Bridge；仍不可用时停止广告采集。
6. 采集 TikTok 和 Shopee；单平台失败不删除另一平台成功数据。
7. 同步订单、运行分析、生成质量报告和经营日报。
8. 发送普通日报；故障另写 JSONL 并发送去重红色故障卡片。

登录触发时：

- 09:00 前只记录 no-op，等待 09:00 日历触发。
- 09:00 后前一日日报未完整成功时补跑。
- 已完整成功时幂等退出。
- partial 或 failed 不写成成功，保留后续补跑资格。

## 人工补跑

从唯一代码源补跑 2026-07-29，禁止普通日报和故障卡片外发：

```bash
.venv/bin/adwatch run daily \
  --mode ziniao --date 2026-07-29 --no-notify
```

`--no-notify` 不影响数据库、质量报告、经营日报和故障日志。完成后检查：

```bash
test -f var/reports/quality-2026-07-29.json
test -f var/reports/daily-2026-07-29.md
```

## 故障排查

主要证据：

```text
var/operations/daily-schedule-state.json
var/logs/adwatch-faults.jsonl
var/logs/launchd.out.log
var/logs/launchd.err.log
```

故障 JSONL 包含执行日、日报日期、阶段、错误类型、脱敏摘要、退出码、通知
状态和去重键。相同日报日期、阶段和错误类型的故障卡片只在首次发送成功前
重试，不重复刷屏。

Bridge 故障先运行：

```bash
ziniao-cli doctor
ziniao-cli store list
```

任务未加载或漂移先运行：

```bash
.venv/bin/adwatch schedule status
.venv/bin/adwatch schedule repair
```

## 重启或重新登录验收

重启或退出登录会中断当前终端和协作会话，必须在取得明确确认后执行。

重新登录后，不要人工运行 `bootstrap`，直接检查：

```bash
.venv/bin/adwatch schedule status
launchctl print "gui/$(id -u)/com.adwatch.daily"
```

验收标准：

1. 服务可查询，且执行路径和工作目录只指向唯一代码源。
2. plist 为 09:00 且 `RunAtLoad=true`。
3. 09:00 前登录时只记录 no-op；09:00 后缺失日报会自动补跑。

## 次日 09:00 持久化验收

真实 09:00 后检查：

```bash
.venv/bin/adwatch schedule status
test -f var/operations/daily-schedule-state.json
tail -n 100 var/logs/launchd.out.log
tail -n 100 var/logs/launchd.err.log
```

同时核对当天质量报告、前一日经营日报和汇率来源字段。只有服务持续存在且
该次运行完整成功，才可把持久化验收标记为完成。
