# AdWatch P1-C 利润回算、经营页与数据可信度实施计划

> **执行技能：** `brainstorming`、`writing-plans`、`executing-plans`、
> `test-driven-development`、`requesting-code-review`、
> `verification-before-completion`。

**目标：** 让每个利润数字都能解释使用了哪些输入、来源日期和降级原因；支持
日期范围确定性重算和差异报告；今日经营页按店铺展示订单、退款、成本、库存、
汇率与结算来源。缺失退款、成本或其他上游时保持 `pending_data`，不得用 0 冒充。

**当前基线：** 迁移版本 23；2026-08-01—03 各有 1 条 Shopee 广告总览，
对应 28 条订单行、21 个有效订单，但尚无订单成本快照和利润结果；当前仍有 7 个
订单 SKU 缺成本或履约版本。Income 周费率与汇率已经可用，退款状态观测没有金额，
不能据此推断退款金额为 0。

## 1. 业务边界

- `profit_results` 只保存输入完整、可验证的利润；缺输入只保存 lineage 和告警。
- 历史 `product_costs.refund_amount=0` 没有来源标记时迁移为 `pending_data`；只有
  明确 CSV 输入或未来逐订单退款来源才可标记 `observed`。
- 广告 GMV/消耗来自当日指标；订单成本优先使用已冻结订单成本快照；平台费率
  继续沿用 P1-B 的覆盖/沿用/legacy 选择；汇率保留真实来源日和回退状态。
- 来源引用只保存表名、业务日期、安全文件名或哈希，不保存完整本机路径、PDF
  正文、订单号列表、买家 PII 或会话字段。
- 日期范围重算只覆盖计算派生表和对应 lineage/告警/建议，不改原始广告、订单、
  成本、库存、汇率或 Income 事实。
- 同一输入重复重算必须得到相同利润和 lineage；差异报告使用 JSON 原子写入。
- TikTok 继续禁采；真实广告写入继续关闭；P1-C 不部署、不提交、不发送飞书。

## 2. 数据结构（迁移 24）

### `product_costs` 来源补强

- `refund_data_status`：`observed|pending_data`，旧行默认 `pending_data`。
- `input_source_name`：安全文件名或 `mock`；旧行为空字符串。

完整/最小经营输入 CSV 明确提供 `refund_amount` 时写 `observed`；mock seed 也写
`observed`。只凭旧默认值或缺少逐订单退款事实时不得升级。

### `profit_input_lineage`

逻辑主键沿用利润结果六维键再加 `input_name`。字段包括：

- `input_name`：`gmv|refunds|commission_rate|product_cost|ad_spend|`
  `seller_shipping|coupons|allocated_fixed_cost|exchange_rate`；
- `status`：`ready|carried_forward|pending_data|ambiguous|not_applicable`；
- `source_type`、`source_date`、`age_days`、`source_ref`、`detail_code`；
- `value_text` 仅保存用于计算的业务数值，缺失保持 `NULL`。

分析每次先重建当日 lineage，再决定能否写利润；因此页面能解释“为何无利润”。

### `analysis_recalculation_runs`

保存运行 ID、起止日期、状态、日期/指标/利润/待补计数、差异报告安全文件名和
起止时间。不得保存完整输出路径或异常正文。

## 3. TDD 执行任务

### C1：迁移 24 与退款输入可信状态

- [x] 先写迁移列、lineage 表、范围运行表、旧库升级和原子回滚测试并确认 RED。
- [x] 完整/最小 CSV 导入把显式退款金额标为 `observed`，mock seed 同样标记。
- [x] 旧 `product_costs` 行升级后保持 `pending_data`；0 不自动等同“无退款”。

Run：`.venv/bin/python -m pytest tests/storage tests/analytics/test_business_inputs.py -q`

### C2：利润输入 lineage 与安全降级

- [x] 先覆盖订单成本快照、成本历史、Income 费率、汇率回退和指标来源。
- [x] 覆盖退款 pending、订单成本多指标分摊 ambiguous、共享 SKU 费率 pending。
- [x] `AnalysisService.run()` 每次确定性重建 lineage；缺输入不写利润但保留信息告警。
- [x] 已验证利润才进入策略建议；`pending_data` 不触发预算/暂停动作。

Run：`.venv/bin/python -m pytest tests/analytics tests/strategy -q`

### C3：日期范围重算与差异报告

- [x] `analyze` 支持二选一：`--date` 或完整的 `--from/--to`；拒绝半个范围和倒序。
- [x] 范围服务只处理范围内有指标的日期，记录 added/changed/unchanged/removed、
  利润数、待补数和各缺失输入计数。
- [x] 默认写 `var/reports/profit-recalculation-<from>-<to>.json`，允许 `--output`
  覆盖；输出使用临时文件原子替换。
- [x] 重复运行第二份差异报告必须为利润/lineage `unchanged`，运行记录可审计。

Run：`.venv/bin/python -m pytest tests/analytics/test_recalculation.py tests/test_cli.py -q`

### C4：读取模型与日报可信度

- [x] `DailySnapshot` 增加按平台/店铺的 `ProfitSourceSummary` 和输入 lineage。
- [x] 汇总广告指标行/归因订单、平台订单、已冻结成本订单、退款金额完整度、最新
  平台库存日期、费率周期和汇率来源日。
- [x] capability 不再只看旧 `product_costs`/`inventory_snapshots`；以实际 lineage
  和平台库存/订单成本事实判定 ready、partial 或 pending。
- [x] 日报列出关键缺口与来源新鲜度，不展示订单号、完整路径或假 0。

Run：`.venv/bin/python -m pytest tests/reporting -q`

### C5：今日经营页来源穿透

- [x] 在首页加入纵向“数据来源与可信度”卡片，每店一张，避免新增横向滚动。
- [x] 展示广告日期、平台订单/归因订单、成本覆盖、退款状态、库存快照、汇率来源、
  结算费率周期和缺失输入；全部文本 HTML escape。
- [x] 净利润待补时卡片明确列出原因；不把 pending 渲染成 ¥0.00。
- [x] `/optimization` 的三种 ROAS 仅在所需输入 ready 时显示，否则显示待补原因，
  策略建议继续受可信度门禁约束。

Run：`.venv/bin/python -m pytest tests/dashboard tests/optimization -q`

### C6：真实 30 天重算与差异验收

- [x] 先备份 production SQLite 并校验；运行本地 `business sync-orders`，只处理
  已有订单/成本/履约事实，不访问平台、不发送飞书。
- [x] 对截至 2026-08-03 的最近 30 天有指标日期执行范围重算；生成差异报告。
- [x] 重点验收 2026-08-01—03：可用订单成本正确冻结；缺成本/履约 SKU
  保持待补；退款金额未知保持 pending，绝不生成虚假已验证利润或建议。
- [x] 第二次重算验证幂等；生产库完整性、lineage 唯一键、报告安全扫描通过。

### C7：文档、审查和完整质量门禁

- [x] 更新 README、数据底座、findings、总计划、主任务和进度。
- [x] 用 `requesting-code-review` 独立审查并修复全部 Critical/Important。
- [x] 运行全量 pytest、Ruff、compileall、`git diff --check` 和生产库完整性检查。

## 4. 完成门槛

- 所有利润输入都有可查询来源/日期/状态；缺失退款或成本不再被当作 0。
- 范围重算结果确定、可审计，第二次运行无派生数据漂移。
- 今日经营页能逐店解释利润已生成或待补的原因，且无横向来源大表。
- 真实 30 天差异报告与 8 月 1—3 日现场状态一致；不发送飞书、不启用 TikTok、
  不开启 Live。
- 全量质量门禁和独立审查通过后，才进入 P1-D；服务器只读版另按部署检查点处理。
