# AdWatch 剩余功能闭环总路线图

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to execute one independently approved batch plan at a time. Every batch also requires `test-driven-development`, `requesting-code-review`, and `verification-before-completion`.

**目标：** 以 `findings.md` 的 45 项端到端能力矩阵为基线，在两个 P0 已完成后，按数据依赖和业务风险实施 24 项剩余缺口，并把当前明确禁采的 TikTok 真实采集保留为有启用条件的后置项。

**架构原则：** SQLite 继续作为唯一事实来源。紫鸟内已登录页面只承担平台只读采集；平台事实先进入独立仓储和运行记录，再由分析、账本、报告、审批消费。所有人工写入必须经过应用服务、CSRF、审计和冲销；任何外部能力失败都要独立记录，不能伪造成功或删除历史事实。

**技术栈：** Python 3.11+、SQLite WAL、标准库 HTTP Server、紫鸟 CLI/ZClaw、pytest、Ruff、openpyxl、pypdf、macOS launchd。

**已批准设计：** `docs/design/2026-08-04-adwatch-remaining-gap-closure-design.md`

---

## 0. 当前基线与实施边界

- 已完成：P1-A 至 P1-D 全部完成（退款/物流/库存事实、Income PDF/结算/平台费、利润回算/经营可信度、财务/库存/分润 Web 闭环）。
- 当前矩阵：28 项已实现、13 项部分实现、4 项未实现；剩余 17 项未完整闭环。
- 当前质量基线：430 tests passed、Ruff 0、compileall 通过。
- TikTok 继续禁采，不纳入近期现场验收；只有用户重新启用后才执行 TikTok Campaign 验收。
- 官方平台 API/OAuth 是可选增强，不作为主线完成门槛。
- 本轮只规划、不执行服务器部署、Live 广告写入或历史数据删除。
- 服务器部署在 P1-C 后设置只读经营版检查点，在 P3 设置正式生产版检查点；实施时必须另写项目专属部署计划。
- 每个批次必须满足：失败测试（RED）→ 最小实现（GREEN）→ 全量回归 → 真实只读样本 → README/运维文档同步。

### 0.1 剩余 25 项覆盖映射

| # | findings 不完整能力 | 批次 | 关闭方式 |
|---:|---|---|---|
| 1 | Shopee Campaign/SKU 明细与分页 | P2-A | 真实分页明细、总览对账与隔离 |
| 2 | TikTok 广告真实采集 | 后置 | 保持禁采；用户重新启用后另立现场计划 |
| 3 | 退款 T+3 回溯 | P1-A/P1-B | 状态观测、金额来源与历史修正 |
| 4 | 物流状态同步 | P1-A | 每日观测事实与新鲜度 |
| 5 | 平台/ERP 库存同步 | P1-A | Shopee 平台库存快照；ERP 连接器保留接口边界 |
| 6 | 结算、Income PDF 与平台费 | P1-B | 文件哈希、周期费率、结算事实 |
| 7 | 广告充值自动同步 | P1-B | 充值事实独立于广告消耗 |
| 8 | 六类实验、A/B 与时间线 | P2-D | 实验状态机、护栏和影响时间线 |
| 9 | 策略规则版本 Web 维护 | P2-B | 受控新增/生效/关闭 |
| 10 | 取消/退款/刷单归因剔除 | P1-A/P1-C | 真实事实接入与利润/ROAS 重算 |
| 11 | browser-use 备用采集 | P2-D | 配置化失败门槛和来源标记 |
| 12 | 采购/出库/退货/报损库存台账 | P1-A/P1-D | 真实上游联动与完整 Web 操作 |
| 13 | 费用/出资/提款/刷单/广告资金账本 | P1-D | 确认、冲销和专属页面闭环 |
| 14 | 现金/平台/银行余额对账 | P1-D | 快照、差额和修复链路 |
| 15 | 分润协议/期间/付款 | P1-D | 真实结算输入和冲销闭环 |
| 16 | 全量写入审计、凭证与冲销 | P1-D | actor、原因、哈希、调整/冲销 |
| 17 | 周报 | P2-C | 周聚合、环比和 TOP/BOTTOM |
| 18 | 月报 | P2-C | 收入、成本、费用、利润和现金 |
| 19 | 今日经营页 | P1-C | 真实上游、来源和降级状态 |
| 20 | 广告调优中心 | P2-A/P2-B | 明细、建议、审批与执行证据 |
| 21 | `/approvals` 审批审计页 | P2-B | 列表、详情、筛选和时间线 |
| 22 | 动作 selector 当前部署验收 | P3 | 唯一代码源证据和 Shadow 验收 |
| 23 | 每周一自动周报调度 | P2-C | 独立锁、补跑和通知去重 |
| 24 | 连续三日不低于 99% 对账 | P3 | 三个自然日真实人工对账 |
| 25 | readiness / launch checklist 完整性 | P3 | 代码与现场能力逐项暴露 |

## 1. 批次依赖

```text
P1-A 退款/物流/库存事实
          │
          ├──────► P1-C 利润重算与经营页可信度
          │
P1-B 结算/平台费/广告充值 ─┘
          │
          └──────► P1-D 财务与对账闭环

P2-A Campaign/SKU 广告明细 ─► P2-B 策略/审批页面 ─► P2-D 实验体系

P2-C 周月报与调度可在 P1-C 后开始
P3 现场验收必须等待所有目标批次完成
```

---

## 2. P1-A：Shopee 退款 T+3、物流与库存事实

**关闭差距：** 退款 T+3 回溯、物流状态同步、平台库存同步、取消/退款归因剔除、今日经营真实订单输入、库存安全门禁数据源。

**精细实施计划：** `docs/plans/2026-08-04-adwatch-p1a-commerce-facts.md`

该计划复用现有 `orders`、`integrations` 与 `order_sync_runs`，不会重复创建宽泛的 `commerce` 仓储包。每日 T+3 回溯天数进入配置；退款金额字段不可用时保存 `missing`，禁止猜测为零。

**完成门槛：** 连续两次运行结果幂等；退款状态可被 T+3 更新，金额来源可解释；物流与库存有独立新鲜度；无买家 PII 落库。

---

## 3. P1-B：Income PDF、结算、平台费与广告充值

**关闭差距：** 结算/Income PDF/平台费同步、广告充值自动同步、日报费率来源、现金与平台余额的上游事实。

**详细子计划：** `docs/plans/2026-08-04-adwatch-p1b-income.md`。该计划已按
当前迁移 22、09:00 日任务、真实 PDF 字段和利润血缘要求刷新；
`2026-07-28` 旧计划仅作为历史设计依据，不再直接执行。

**Files:**

- Create: `src/adwatch/income/models.py`
- Create: `src/adwatch/income/shopee_pdf.py`
- Create: `src/adwatch/income/repository.py`
- Create: `src/adwatch/income/service.py`
- Create: `src/adwatch/income/__init__.py`
- Modify: `src/adwatch/integrations/commerce.py`
- Modify: `src/adwatch/storage/migrations.py`
- Modify: `src/adwatch/operations/daily_run.py`
- Modify: `src/adwatch/analytics/service.py`
- Modify: `src/adwatch/reporting/read_model.py`
- Modify: `src/adwatch/reporting/markdown.py`
- Modify: `src/adwatch/cli.py`
- Modify: `src/adwatch/config.py`
- Modify: `.env.example`
- Modify: `pyproject.toml`
- Create: `tests/income/test_shopee_pdf.py`
- Create: `tests/income/test_repository.py`
- Create: `tests/income/test_service.py`
- Create: `tests/test_income_cli.py`
- Modify: `tests/integrations/test_commerce.py`
- Modify: `tests/operations/test_daily_run.py`

- [x] **Task 3.1：Income 文件哈希、隔离和费率版本迁移**

  同一 PDF 哈希重复扫描必须幂等；重名不同内容必须保留版本；无法确定店铺/周期/币种的文件进入隔离区。

- [x] **Task 3.2：Shopee PDF 解析器**

  用固定字段别名解析商品收入、佣金、服务费、交易费、联盟费、运费、补贴、广告充值和调整项；总额不平时拒绝导入并给出差异。

- [x] **Task 3.3：`income import|scan|list` CLI**

  扫描只读取配置目录；命令输出导入/重复/隔离计数，不输出敏感文件正文。

- [x] **Task 3.4：每日扫描与利润费率选择**

  选择顺序为“覆盖订单日的结算周期 → 此前最近有效周期 → 旧成本费率”；来源必须写入利润结果和日报。

- [x] **Task 3.5：广告充值与结算入账**

  将 Income 中可审计的充值、结算和平台费写入 `CommerceRepository`，保持“充值不等于广告消耗”“结算不等于收入确认”的会计口径。

Run: `.venv/bin/python -m pytest tests/income tests/integrations/test_commerce.py tests/analytics tests/reporting -q`

**完成门槛：** 一份真实脱敏 PDF 可导入并对平；费率选择可追溯；重复扫描不重复入账；日报显示结算周期和来源。

---

## 4. P1-C：利润回算、经营页与数据可信度

**关闭差距：** 今日经营页、六层利润上游降级、三种 ROAS 安全门禁、生产建议长期为空的问题。

**详细子计划：** `docs/plans/2026-08-04-adwatch-p1c-profit-traceability.md`。
本批使用迁移 24，先建立利润输入来源与退款可信状态，再做范围重算和页面穿透。

**Files:**

- Modify: `src/adwatch/analytics/service.py`
- Modify: `src/adwatch/analytics/profit.py`
- Modify: `src/adwatch/optimization/service.py`
- Modify: `src/adwatch/reporting/read_model.py`
- Modify: `src/adwatch/reporting/markdown.py`
- Modify: `src/adwatch/dashboard/app.py`
- Modify: `src/adwatch/dashboard/views.py`
- Modify: `src/adwatch/cli.py`
- Modify: `tests/analytics/test_profit.py`
- Modify: `tests/analytics/test_service.py`
- Modify: `tests/optimization/test_service.py`
- Modify: `tests/reporting/test_read_model.py`
- Modify: `tests/reporting/test_markdown.py`
- Modify: `tests/dashboard/test_app.py`

- [x] **Task 4.1：给每个利润输入增加来源与新鲜度契约**
- [x] **Task 4.2：新增日期范围重算命令，旧结果按确定性规则覆盖**
- [x] **Task 4.3：经营页穿透到订单数、退款、成本、库存和结算来源**
- [x] **Task 4.4：补算 P1 上线日前 30 天并生成差异报告**

Run: `.venv/bin/adwatch analyze --from 2026-07-01 --to 2026-07-30`

**完成门槛：** 利润结果不再把缺失上游当 0；同一输入重算结果一致；页面可解释每个利润数字的来源。

---

## 5. P1-D：财务、库存和分润 Web 闭环

**关闭差距：** 费用确认/冲销、凭证、收入调整、银行余额、三方余额差额、分润冲销、全量人工写入审计。

**Files:**

- Modify: `src/adwatch/storage/migrations.py`
- Modify: `src/adwatch/ledger/models.py`
- Modify: `src/adwatch/ledger/service.py`
- Modify: `src/adwatch/inventory/service.py`
- Modify: `src/adwatch/profit_sharing/service.py`
- Modify: `src/adwatch/reconciliation/service.py`
- Modify: `src/adwatch/dashboard/routes.py`
- Modify: `src/adwatch/dashboard/views.py`
- Modify: `src/adwatch/dashboard/app.py`
- Modify: `tests/ledger/test_service.py`
- Modify: `tests/inventory/test_service.py`
- Modify: `tests/profit_sharing/test_service.py`
- Modify: `tests/reconciliation/test_service.py`
- Modify: `tests/dashboard/test_routes.py`
- Modify: `tests/dashboard/test_views.py`

- [x] **Task 5.1：费用确认与冲销状态机**
- [x] **Task 5.2：凭证元数据、文件哈希和安全下载**
- [x] **Task 5.3：银行余额快照与现金/平台/银行差额**
- [x] **Task 5.4：收入调整、人工库存调整和分润冲销**
- [x] **Task 5.5：把写入口放回 `/ad-funds`、`/profit-sharing` 等专属页面**

Run: `.venv/bin/python -m pytest tests/ledger tests/inventory tests/profit_sharing tests/reconciliation tests/dashboard -q`

**完成门槛：** 所有财务事实只增补/冲销不物理删除；每次写入有 actor、时间、原因和审计事件；页面完成一次端到端写入与冲销验收。

---

## 6. P2-A：Shopee Campaign/SKU 广告明细与分页

**关闭差距：** 当前生产只有 `campaign_id="Shop GMV Max"`、`sku_id="__ALL__"` 的汇总行，调优与归因无法下钻。

**Files:**

- Modify: `src/adwatch/collectors/ziniao.py`
- Modify: `src/adwatch/collectors/ziniao_client.py`
- Modify: `src/adwatch/pipeline/validation.py`
- Modify: `src/adwatch/storage/metrics.py`
- Modify: `src/adwatch/reporting/read_model.py`
- Modify: `src/adwatch/dashboard/views.py`
- Modify: `tests/collectors/test_ziniao.py`
- Modify: `tests/collectors/test_ziniao_client.py`
- Modify: `tests/pipeline/test_validation.py`
- Modify: `tests/reporting/test_read_model.py`
- Modify: `tests/dashboard/test_views.py`

- [ ] **Task 6.1：用真实响应夹具固定分页和字段契约**
- [ ] **Task 6.2：采集 Campaign 和商品广告明细，保留总览作对账层**
- [ ] **Task 6.3：按 Campaign/SKU 汇总并校验与总览差额**
- [ ] **Task 6.4：真实单日采集对账，差额超阈值进入隔离而非覆盖**

Run: `.venv/bin/python -m pytest tests/collectors tests/pipeline tests/reporting -q`

**完成门槛：** 生产至少一个有投放日存在非 `__ALL__` 明细；明细合计与平台总览在配置容差内；所有分页无重复/漏页。

---

## 7. P2-B：策略规则维护与审批审计页面

**关闭差距：** 策略规则页只读、`/approvals` 占位、执行审计无可视入口、现场无完整审批记录。

**Files:**

- Modify: `src/adwatch/strategy/service.py`
- Modify: `src/adwatch/approval/service.py`
- Modify: `src/adwatch/execution/executor.py`
- Modify: `src/adwatch/dashboard/routes.py`
- Modify: `src/adwatch/dashboard/views.py`
- Modify: `src/adwatch/dashboard/app.py`
- Modify: `tests/strategy/test_service.py`
- Modify: `tests/approval/test_service.py`
- Modify: `tests/execution/test_executor.py`
- Modify: `tests/dashboard/test_routes.py`
- Modify: `tests/dashboard/test_views.py`

- [ ] **Task 7.1：策略版本新增/生效/关闭 Web 路由与校验**
- [ ] **Task 7.2：实现 `/approvals` 列表、筛选、详情与过期状态**
- [ ] **Task 7.3：实现执行前后证据、回读、回滚和熔断审计时间线**
- [ ] **Task 7.4：在 Shadow 模式完成一条建议→审批→执行→审计闭环**

**完成门槛：** 页面不再落入占位页；规则版本不能重叠；审批和执行证据可追溯；不启用 Live 写入。

---

## 8. P2-C：周报、月报和每周调度

**关闭差距：** 周报只是日报拼接、月报只有广告汇总、每周一没有自动任务。

**Files:**

- Create: `src/adwatch/reporting/periodic.py`
- Modify: `src/adwatch/reporting/read_model.py`
- Modify: `src/adwatch/reporting/markdown.py`
- Modify: `src/adwatch/operations/scheduler.py`
- Modify: `src/adwatch/operations/run_state.py`
- Modify: `src/adwatch/cli.py`
- Modify: `tests/reporting/test_read_model.py`
- Create: `tests/reporting/test_periodic.py`
- Modify: `tests/operations/test_scheduler.py`
- Modify: `tests/operations/test_run_state.py`
- Modify: `tests/test_cli.py`
- Modify: `docs/operations/adwatch-macos-schedule.md`

- [ ] **Task 8.1：周维度聚合、环比、TOP/BOTTOM 和风险摘要**
- [ ] **Task 8.2：月经营收入、成本、费用、净利润和现金变化**
- [ ] **Task 8.3：周期报告幂等、缺日提示和来源完整度**
- [ ] **Task 8.4：每周一独立调度、登录补跑、锁和故障通知**

**完成门槛：** 周/月报告是周期聚合而非日报拼接；每周任务与每日任务独立锁和状态；重跑不重复通知。

---

## 9. P2-D：实验体系与备用采集

**关闭差距：** 六类实验、A/B、操作影响时间线、browser-use 备用采集切换。

**Files:**

- Create: `src/adwatch/experiments/models.py`
- Create: `src/adwatch/experiments/service.py`
- Create: `src/adwatch/experiments/__init__.py`
- Modify: `src/adwatch/storage/migrations.py`
- Modify: `src/adwatch/strategy/rules.py`
- Modify: `src/adwatch/dashboard/routes.py`
- Modify: `src/adwatch/dashboard/views.py`
- Create: `src/adwatch/collectors/fallback.py`
- Modify: `src/adwatch/operations/daily_run.py`
- Create: `tests/experiments/test_service.py`
- Modify: `tests/strategy/test_rules.py`
- Modify: `tests/dashboard/test_routes.py`
- Create: `tests/collectors/test_fallback.py`
- Modify: `tests/operations/test_daily_run.py`

- [ ] **Task 9.1：实验分组、窗口、护栏和结果状态机**
- [ ] **Task 9.2：商品测试池、素材 A/B 与操作影响时间线**
- [ ] **Task 9.3：主采集连续失败后的备用后端切换与证据记录**

**完成门槛：** 实验不会和普通策略同时改写同一对象；结果有样本量与置信状态；备用采集只在明确门槛后启用并标记来源。

---

## 10. P3：上线清单、证据迁移、服务器部署与生产验收

**关闭差距：** selector 证据旧路径、连续三日对账未达标、readiness/launch-checklist 漏报、当前部署成熟度不足。

**Files:**

- Modify: `src/adwatch/operations/readiness.py`
- Modify: `src/adwatch/operations/launch_checklist.py`
- Modify: `src/adwatch/execution/activation.py`
- Modify: `src/adwatch/cli.py`
- Modify: `tests/operations/test_readiness.py`
- Modify: `tests/operations/test_launch_checklist.py`
- Modify: `tests/execution/test_activation.py`
- Modify: `README.md`
- Modify: `docs/operations/adwatch-macos-schedule.md`
- Modify: `docs/operations/data-foundation.md`
- Modify: `findings.md`
- Create: `Dockerfile`
- Create: `compose.yaml`
- Create: `.dockerignore`
- Create: `.deploy_config.example`
- Create: `scripts/deploy/package.sh`
- Create: `scripts/deploy/deploy.sh`
- Create: `scripts/deploy/verify.sh`
- Create: `scripts/deploy/rollback.sh`
- Create: `docs/operations/adwatch-server-deployment.md`

- [ ] **Task 10.1：把订单、退款、库存、结算、Campaign 明细加入 readiness**
- [ ] **Task 10.2：重新登记唯一代码源下的 selector 截图与版本证据**
- [ ] **Task 10.3：完成 Shadow、回滚、故障通知和重启后调度演练**
- [ ] **Task 10.4：连续 3 天逐项人工对账，单日准确率均不低于 99%**
- [ ] **Task 10.5：重新运行 45 项矩阵审计并同步 README**
- [ ] **Task 10.6：按项目专属部署计划完成只读服务部署、备份恢复、版本哈希和健康检查**

Run:

```bash
.venv/bin/adwatch doctor
.venv/bin/adwatch readiness
.venv/bin/adwatch launch-checklist --format markdown
.venv/bin/adwatch schedule status
PYTHONPYCACHEPREFIX=/tmp/adwatch-pycache .venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
PYTHONPYCACHEPREFIX=/tmp/adwatch-pycache .venv/bin/python -m compileall -q src tests
git diff --check
```

**完成门槛：** checklist 不再把缺代码误报成只差配置；所有证据路径位于唯一代码源；连续三日对账通过；全量质量门禁为绿色；服务器只有一个项目目录和应用实例，SQLite 可恢复，容器版本与本地发布版本一致。

---

## 11. 建议执行顺序与检查点

1. 先执行 P1-A；检查真实退款、物流、库存字段是否稳定。字段不稳定时暂停后续利润接入，只保留隔离样本。
2. 再执行 P1-B；用一份真实脱敏 Income PDF 完成对账后，才进入 P1-C。
3. P1-C 完成后回算 30 天并由业务方确认利润口径，这是进入财务页面闭环的检查点。
4. P1-D 完成后再开放对应 Web 写入口，避免页面先于状态机上线。
5. P2-A 必须先于策略/审批真实验收；没有 Campaign/SKU 明细时不允许把策略输出标为生产可用。
6. P2-B 在 P2-A 数据契约冻结后开始；P2-C 在 P1-C 完成后可独立开始，每个批次仍独立回归。
7. P2-D 属于增强项；若优先追求服务器上线，可在用户确认后延后，不阻塞只读经营系统上线。
8. 最后统一执行 P3；未完成三日对账前保持 `ADWATCH_LIVE_WRITES=false`。

## 12. 每批交付物

- 失败测试和通过后的测试证据。
- 迁移版本、幂等键和回滚/恢复说明。
- 一份脱敏真实样本与计数对账，不保存会话令牌和买家 PII。
- 对应 README、运维手册和 `findings.md` 状态更新。
- 代码审查发现与未关闭风险。
- 未经用户明确要求，不提交、不推送、不部署。
