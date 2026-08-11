# 数据底座操作手册

## 运行目录

`ADWATCH_DATA_DIR` 指定所有运行数据的位置：

- `adwatch.sqlite3`：主数据库。
- `reports/`：数据质量报告。
- 后续阶段的下载报表、截图和日报也将放在该目录下。

运行目录、`.env`、SQLite 文件和 Python 缓存均已被 Git 忽略。

## 初始化与健康检查

```bash
source .venv/bin/activate
export ADWATCH_DATA_DIR="$PWD/var"
python -m adwatch init
python -m adwatch doctor
```

`init` 可重复执行，只应用尚未执行的数据库迁移。`doctor` 当前显示 SQLite 路径和紫鸟配置状态。

## 模拟采集

```bash
python -m adwatch collect --mode mock --date 2026-07-22
```

每个平台生成四条确定性记录。相同平台和日期每次生成相同内容，方便重放和自动化测试。质量报告明确标记为模拟数据。

## 幂等与重跑

每日指标的逻辑唯一键由以下字段组成：

1. 平台
2. 店铺
3. 广告账户
4. Campaign
5. SKU
6. 数据日期

再次采集相同键时更新指标，不插入重复行。每次采集仍会新增独立的 `collection_runs` 审计记录。

## Shopee 经营事实回溯

每天 09:00 的生产流程会在广告采集前运行 Shopee 只读同步。默认日期范围是
报告日及之前 3 天，可通过以下配置调整：

```dotenv
ADWATCH_SHOPEE_HISTORY_LOOKBACK_DAYS=3
```

同步写入四组事实：

- `platform_order_lines`：订单 SKU 行。
- `platform_refund_observations`：按订单、观测日保存退款状态；金额未知为 `NULL`。
- `platform_logistics_observations`：按订单、观测日保存物流状态。
- `platform_inventory_snapshots`：按 Item/Model、自然日保存平台可用库存。

退款、物流和库存表的唯一键都包含观测日/快照日。同一自然日重复同步且业务内容
不变时不会产生新写入；只有观测时间变化也不算业务变更，跨自然日则保留历史。
`order_sync_runs` 保存同步起止日期以及三类事实的变更计数。当前 Shopee 订单卡
没有稳定退款金额字段，因此该字段保持缺失，后续必须由逐订单退款明细来源补齐，
禁止从聚合 Income 结算账单反推单笔退款，更不能把未知金额当成 0。

本地订单处理会先用 `store_aliases` 解析紫鸟原始店铺 ID 与规范店铺名，再按订单
发生日选择已生效的成本和履约版本。解析成功后写入不可变的订单成本与履约快照；
修改当前成本不会改写已冻结的历史订单。

## Shopee Income 周结算

每天 09:00 在 Bridge 检查前扫描 `ADWATCH_INCOME_SCAN_ROOT` 下的
`weekly_report_*.pdf`。本机默认扫描紫鸟浏览器数据根目录，服务器部署时必须
显式配置挂载目录：

```dotenv
ADWATCH_INCOME_SCAN_ROOT=/data/adwatch/income
```

可手工执行：

```bash
.venv/bin/adwatch income import --platform shopee --file /path/to/report.pdf
.venv/bin/adwatch income scan
.venv/bin/adwatch income list
```

系统按文件 SHA-256 幂等，保存同周期修订历史，并把结算摘要、平台费用和广告
充值观测分表落库。广告充值不是广告消耗，平台 payout 只标记为结算事实，不直接
冒充订单收入。利润费率依次选择覆盖数据日的周期、此前最近周期、历史成本费率；
选择结果、周期、文件名和哈希均写入利润血缘。日志和日报不保存 PDF 正文、完整
本机路径、姓名、地址或银行信息。

## 利润输入来源与范围重算

迁移 24 为显式退款输入增加 `observed/pending_data` 状态，并创建
`profit_input_lineage`。每个广告指标行固定记录九项利润输入：GMV、退款、平台
费率、商品成本、广告花费、卖家运费、优惠券、分摊固定成本和汇率。每项都包含
状态、来源类型、来源日期、新鲜度和安全来源引用；缺一项就不生成已验证利润。
存在真实平台订单时，商品成本必须覆盖当日所有非取消订单 SKU 行；只覆盖部分
订单行会记录 `order_cost_coverage_incomplete`，不能用部分合计或通用 SKU 成本
补成“已验证利润”。完整的冻结订单快照优先于旧订单成本导入，旧导入只在没有
完整冻结快照时兜底。

单日分析：

```bash
.venv/bin/adwatch analyze --date 2026-08-03
```

范围重算：

```bash
.venv/bin/adwatch analyze \
  --from 2026-07-05 --to 2026-08-03
```

范围模式只处理存在广告指标的日期，原子写入 JSON 差异报告，并在
`analysis_recalculation_runs` 保存成功/失败、处理天数、指标数、利润数和待补数。
所有日期共享一个数据库事务；任一天失败会回滚整个范围，失败审计不会谎报部分
成功。
第二次重算应把未变化的利润与 lineage 计入 `unchanged`。重算不发送飞书，也不
触发真实广告写入。

## 财务闭环与受管凭证

迁移 25 新增：

- `evidence_documents`：业务对象关联、安全展示名、受管文件名、MIME、大小和
  SHA-256；同一对象的相同内容幂等。
- `financial_adjustments`：收入、现金和余额调整；反向调整通过 `reversal_of`
  追加，唯一索引阻止重复冲销。
- `balance_snapshots`：现金、平台和银行余额观测；同一账户修订只追加。
- `balance_reconciliations`：账本现金与三类观测的逐项差额；缺来源保存 NULL，
  解决时新增关联 adjustment 的 superseding 行，原对账行不更新。

库存 movement 和分润付款也使用唯一 `reversal_of` 约束。出资、提款、广告资金、
刷单成本和采购通过保留原行、更新终态并追加反向现金/库存事实完成冲销。所有
人工入口保存 actor、时间、原因（适用时）和 `audit_events`。

凭证目录可配置：

```dotenv
ADWATCH_EVIDENCE_ROOT=./var/evidence
```

服务只接受 PDF/JPEG/PNG 内容，最大 5 MiB；用户文件名不参与磁盘路径。元数据
事务失败会清理本次文件，下载时会再次核对大小、哈希和魔数。

## 数据隔离

空标识、负数、未来日期和未知币种不会写入指标表，而会保存到 `quarantined_records`。对应原因保存在 `issues_json`，批次汇总保存在 `quality_checks`。

可用 SQLite 客户端或 DBeaver 查看：

```sql
SELECT run_id, raw_json, issues_json
FROM quarantined_records
ORDER BY id DESC;
```

## 备份

使用 SQLite 在线备份接口创建并立即校验备份，不直接复制 WAL 运行中的数据库：

```bash
.venv/bin/adwatch backup create \
  --output var/backups/adwatch-before-change.sqlite3
.venv/bin/adwatch backup verify \
  --path var/backups/adwatch-before-change.sqlite3
```

恢复时停止程序，用备份文件替换 `adwatch.sqlite3`，然后执行：

```bash
.venv/bin/adwatch init
```

该命令会补齐备份版本之后新增的迁移。

## 紫鸟真实适配器前置配置

```dotenv
ZINIAO_COMPANY=企业名称
ZINIAO_USERNAME=自动化成员用户名
ZINIAO_PASSWORD=自动化成员密码
ZINIAO_ENDPOINT=http://127.0.0.1:1886
```

敏感值不得写入仓库。缺失任一变量时，真实采集会明确失败，不会使用模拟数据代替。
