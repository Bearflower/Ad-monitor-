# AdWatch 第二至第五批一体化系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 macOS 本地完成统一 Web 写入、订单进销存、真实经营数据与广告调优闭环，并产出可执行的第五批现场验收工具。

**Architecture:** 继续以 SQLite 为唯一事实来源，在现有采集、分析、审批和执行模块旁增加聚焦的 `operations_ledger`、`inventory`、`profit_sharing` 与 `optimization` 服务。Web 使用现有标准库 HTTP 服务，不引入新的前端框架；所有写入通过应用服务完成校验、审计和冲销。真实平台能力通过明确的适配器合同接入，缺少现场数据时返回 `pending_external`，绝不伪造成功。

**Tech Stack:** Python 3.11+、SQLite、标准库 `http.server`、pytest、openpyxl、紫鸟 CLI、飞书 Webhook。

---

## 文件结构

- `src/adwatch/storage/migrations.py`：只负责数据库版本和约束。
- `src/adwatch/ledger/models.py`：费用、资金、广告资金和刷单记录值对象。
- `src/adwatch/ledger/service.py`：草稿、确认、冲销和审计事务。
- `src/adwatch/inventory/models.py`：采购和库存移动值对象。
- `src/adwatch/inventory/service.py`：采购入库、销售出库、退货和余额。
- `src/adwatch/profit_sharing/service.py`：协议版本、期间结算和支付。
- `src/adwatch/optimization/models.py`：三种 ROAS、证据与可信度。
- `src/adwatch/optimization/service.py`：经营事实到诊断和策略上下文。
- `src/adwatch/integrations/commerce.py`：收入、广告资金、物流和平台费合同。
- `src/adwatch/reconciliation/service.py`：三日准确率、分类差异和门禁。
- `src/adwatch/dashboard/routes.py`：HTTP 路由与输入解析。
- `src/adwatch/dashboard/views.py`：统一 Web 页面渲染。
- `src/adwatch/dashboard/app.py`：服务器装配，不承载业务规则。

## Task 1：归并第一批订单成本分支

**Files:**
- Merge: `codex/semi-automatic-sku-cost`
- Verify: `tests/orders/`
- Verify: `tests/analytics/test_sku_cost_workbook.py`

- [ ] **Step 1: 在功能工作树运行现有测试**

Run: `.venv/bin/python -m pytest -q`
Expected: `149 passed` 或更多，且 0 failures。

- [ ] **Step 2: 快进归并到 main**

Run: `git merge --ff-only codex/semi-automatic-sku-cost`
Expected: main 包含 `src/adwatch/orders/` 和 SKU 成本工作簿命令。

- [ ] **Step 3: 在 main 重新运行完整测试**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures。

## Task 2：统一经营账与审计底座

**Files:**
- Modify: `src/adwatch/storage/migrations.py`
- Create: `src/adwatch/ledger/__init__.py`
- Create: `src/adwatch/ledger/models.py`
- Create: `src/adwatch/ledger/service.py`
- Test: `tests/storage/test_operations_migration.py`
- Test: `tests/ledger/test_service.py`

- [ ] **Step 1: 写迁移失败测试**

测试迁移后存在 `expense_entries`、`capital_entries`、`withdrawal_entries`、
`ad_funding_entries`、`ad_spend_entries`、`review_order_costs`、
`cash_movements` 和 `audit_events`，并验证金额字段非空、状态和逻辑作废约束。

Run: `.venv/bin/python -m pytest tests/storage/test_operations_migration.py -q`
Expected: FAIL，因为表尚不存在。

- [ ] **Step 2: 添加迁移**

每个金额事实保存 `amount_original`、`currency`、`rate_to_cny` 和
`amount_cny`。`expense_entries` 保存 `fund_nature`、`payer`、
`affects_profit`、`affects_capital` 和 `status`。所有业务表使用稳定
`external_key` 或 UUID 防重。

- [ ] **Step 3: 写服务失败测试**

```python
entry = service.create_expense(
    ExpenseDraft(
        occurred_on=date(2026, 7, 28),
        category="物流",
        amount_original=Decimal("100"),
        currency="CNY",
        rate_to_cny=Decimal("1"),
        payer="洁云",
        fund_nature="operating_expense",
        affects_profit=True,
        affects_capital=False,
    ),
    actor="yl",
)
service.confirm(entry.id, actor="yl")
service.reverse(entry.id, actor="yl", reason="重复录入")
```

断言确认时生成现金移动和审计事件，冲销生成反向流水而非删除原记录；
非法状态跳转、负金额和缺少冲销原因必须失败。

Run: `.venv/bin/python -m pytest tests/ledger/test_service.py -q`
Expected: FAIL，因为服务尚不存在。

- [ ] **Step 4: 实现最小账务服务并验证**

Run: `.venv/bin/python -m pytest tests/storage/test_operations_migration.py tests/ledger/test_service.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/adwatch/storage/migrations.py src/adwatch/ledger tests/storage/test_operations_migration.py tests/ledger/test_service.py
git commit -m "feat: add auditable operations ledger"
```

## Task 3：采购、出库与库存恒等式

**Files:**
- Modify: `src/adwatch/storage/migrations.py`
- Create: `src/adwatch/inventory/__init__.py`
- Create: `src/adwatch/inventory/models.py`
- Create: `src/adwatch/inventory/service.py`
- Test: `tests/storage/test_inventory_migration.py`
- Test: `tests/inventory/test_service.py`

- [ ] **Step 1: 写失败测试**

覆盖采购确认生成 `purchase_in`，有效订单生成 `sale_out`，取消订单不出库，
退货生成 `sale_return`，报损生成 `damage`，同一 `source_type/source_id/sku`
重复处理保持幂等。

Run: `.venv/bin/python -m pytest tests/inventory/test_service.py -q`
Expected: FAIL。

- [ ] **Step 2: 添加采购与库存迁移**

建立 `purchase_receipts`、`purchase_lines`、`inventory_movements`、
`inventory_balances` 和 `order_cost_snapshots`。库存移动数量使用有符号
整数，余额由同事务更新并禁止无授权负库存。

- [ ] **Step 3: 实现库存服务**

```python
balance = (
    opening_units
    + purchase_in
    + sale_return
    - sale_out
    - damage
    + manual_adjustment
)
```

订单成本按订单日期匹配成本历史并保存快照；成本缺失时订单进入
`pending_cost`，不生成伪造的零成本。

- [ ] **Step 4: 验证并提交**

Run: `.venv/bin/python -m pytest tests/storage/test_inventory_migration.py tests/inventory/test_service.py tests/orders -q`
Expected: PASS。

```bash
git add src/adwatch/storage/migrations.py src/adwatch/inventory tests/storage/test_inventory_migration.py tests/inventory tests/orders
git commit -m "feat: add purchasing and inventory movements"
```

## Task 4：分润协议、期间结算与支付

**Files:**
- Modify: `src/adwatch/storage/migrations.py`
- Create: `src/adwatch/profit_sharing/__init__.py`
- Create: `src/adwatch/profit_sharing/service.py`
- Test: `tests/profit_sharing/test_service.py`

- [ ] **Step 1: 写失败测试**

测试默认协议洁云 `0.60`、苏姐 `0.40`；新比例必须新增带生效日期的协议；
旧期间仍引用旧协议；结算草稿可以改区间，确认后只能冲销；实付金额、
日期、状态和备注可受控登记。

Run: `.venv/bin/python -m pytest tests/profit_sharing/test_service.py -q`
Expected: FAIL。

- [ ] **Step 2: 添加迁移与服务**

建立 `profit_share_agreements`、`profit_periods`、`profit_allocations`
和 `profit_payments`。协议同一日期只有一个有效版本，比例总和必须为 1。
期间净利润只扣销售出库成本，不再次扣采购付款；广告只扣实际消耗，
不扣广告充值。

- [ ] **Step 3: 验证并提交**

Run: `.venv/bin/python -m pytest tests/profit_sharing/test_service.py tests/analytics/test_profit.py -q`
Expected: PASS。

```bash
git add src/adwatch/storage/migrations.py src/adwatch/profit_sharing tests/profit_sharing
git commit -m "feat: add versioned partner profit sharing"
```

## Task 5：统一 Web 写入与业务导航

**Files:**
- Create: `src/adwatch/dashboard/routes.py`
- Create: `src/adwatch/dashboard/views.py`
- Modify: `src/adwatch/dashboard/app.py`
- Test: `tests/dashboard/test_routes.py`
- Test: `tests/dashboard/test_views.py`
- Modify: `tests/dashboard/test_app.py`

- [ ] **Step 1: 写路由失败测试**

覆盖 SKU 成本、采购、费用/前期投入、刷单成本、出资、提款、广告补录、
分润协议与支付的 GET/POST。缺少 CSRF token、非法 Decimal、未知币种、
空原因和错误状态跳转返回 400/409，不写数据库。

Run: `.venv/bin/python -m pytest tests/dashboard/test_routes.py -q`
Expected: FAIL。

- [ ] **Step 2: 实现路由和 PRG**

使用 `POST → 303 → GET`，会话级 CSRF token，HTML 转义和服务层校验。
HTTP 层不直接编写 SQL。

- [ ] **Step 3: 写页面失败测试**

断言统一导航包含“今日经营、广告调优、收入与广告资金、SKU与库存、
记账对账、合伙人分润、审批执行”；表单展示来源、状态、凭证、审计和
冲销入口。

- [ ] **Step 4: 拆分渲染并验证**

Run: `.venv/bin/python -m pytest tests/dashboard -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/adwatch/dashboard tests/dashboard
git commit -m "feat: add unified operations web interface"
```

## Task 6：收入、广告资金、退款、物流与平台费用合同

**Files:**
- Create: `src/adwatch/integrations/commerce.py`
- Modify: `src/adwatch/integrations/refunds.py`
- Modify: `src/adwatch/integrations/inventory.py`
- Modify: `src/adwatch/integrations/exchange_rates.py`
- Modify: `src/adwatch/pipeline/runner.py`
- Test: `tests/integrations/test_commerce.py`
- Modify: `tests/pipeline/test_runner.py`

- [ ] **Step 1: 写合同失败测试**

定义 `SettlementRecord`、`AdFundingRecord`、`AdSpendRecord`、
`RefundRecord`、`LogisticsRecord` 和 `PlatformFeeRecord`。适配器返回
`ready/pending_data/pending_external/blocked` 与可追溯来源，不允许用
空数组表示“成功但无数据”。

- [ ] **Step 2: 实现合同与批量幂等写入**

CLI 可获取的事实自动写入；不支持的能力标记 `pending_external` 并允许
人工补录。平台原始记录不可被人工覆盖，人工更正使用调整单。

- [ ] **Step 3: 接入每日流水线**

顺序固定为订单/广告原始事实、状态退款物流、汇率平台费、成本库存、
利润与策略。单一适配器失败不得伪造其他适配器成功。

- [ ] **Step 4: 验证并提交**

Run: `.venv/bin/python -m pytest tests/integrations tests/pipeline -q`
Expected: PASS。

```bash
git add src/adwatch/integrations src/adwatch/pipeline tests/integrations tests/pipeline
git commit -m "feat: add commerce integration contracts"
```

## Task 7：三种 ROAS 与广告调优证据链

**Files:**
- Modify: `src/adwatch/storage/migrations.py`
- Create: `src/adwatch/optimization/__init__.py`
- Create: `src/adwatch/optimization/models.py`
- Create: `src/adwatch/optimization/service.py`
- Modify: `src/adwatch/analytics/anomalies.py`
- Modify: `src/adwatch/analytics/service.py`
- Test: `tests/optimization/test_service.py`
- Modify: `tests/analytics/test_anomalies.py`

- [ ] **Step 1: 写指标失败测试**

```python
assert result.platform_roas == attributed_gmv / ad_spend
assert result.net_sales_roas == real_net_sales / ad_spend
assert result.profit_roas == pre_ad_contribution_margin / ad_spend
assert result.post_ad_net_profit == pre_ad_contribution_margin - ad_spend
```

刷单、取消和退款必须从真实销售中扣除；广告充值不得进入分母或利润；
无法可靠归因到 SKU 时返回 Campaign 级结果和 `campaign_only` 能力。

- [ ] **Step 2: 添加规则版本和证据迁移**

给建议保存 `rule_version_id`、`window_days`、`confidence_level`、
`evidence_json`、`expected_before_json` 和 `expected_impact_json`；
建立带生效日期的 `strategy_rule_versions`。

- [ ] **Step 3: 实现诊断**

覆盖花费突增、ROAS 连降、广告后亏损、退款/取消/刷单异常、库存风险、
停止消耗、数据缺失、预算失衡和广告余额不足。每项诊断返回证据引用，
不只返回自然语言。

- [ ] **Step 4: 验证并提交**

Run: `.venv/bin/python -m pytest tests/optimization tests/analytics -q`
Expected: PASS。

```bash
git add src/adwatch/storage/migrations.py src/adwatch/optimization src/adwatch/analytics tests/optimization tests/analytics
git commit -m "feat: add evidence based ad optimization"
```

## Task 8：利润、库存和可信度驱动的策略门禁

**Files:**
- Modify: `src/adwatch/strategy/rules.py`
- Modify: `src/adwatch/analytics/service.py`
- Test: `tests/strategy/test_rules.py`
- Modify: `tests/analytics/test_service.py`

- [ ] **Step 1: 写门禁失败测试**

分别证明学习期内不暂停；负利润、库存不足、高退款和低可信度均禁止
加预算；连续三天低于目标 50% 才建议暂停；复测池不超过可分配预算
20%；每条建议包含规则版本、证据、前后值和审批等级。

- [ ] **Step 2: 扩展 StrategyContext**

加入 `net_sales_roas`、`profit_roas`、`refund_rate`、
`data_confidence`、`rule_version_id` 和证据引用。现有平台 ROAS 规则
保留，但加预算与暂停必须通过真实利润和可信度门禁。

- [ ] **Step 3: 验证并提交**

Run: `.venv/bin/python -m pytest tests/strategy tests/analytics/test_service.py -q`
Expected: PASS。

```bash
git add src/adwatch/strategy src/adwatch/analytics/service.py tests/strategy tests/analytics/test_service.py
git commit -m "feat: gate ad actions with business evidence"
```

## Task 9：广告调优中心与审批证据

**Files:**
- Modify: `src/adwatch/reporting/read_model.py`
- Modify: `src/adwatch/dashboard/views.py`
- Modify: `src/adwatch/dashboard/routes.py`
- Modify: `src/adwatch/approval/feishu.py`
- Test: `tests/reporting/test_read_model.py`
- Modify: `tests/dashboard/test_views.py`
- Modify: `tests/approval/test_feishu.py`

- [ ] **Step 1: 写失败测试**

看板必须展示三种 ROAS、3/7/14/30 天趋势、可信度、建议前后值、
预计影响、订单/退款/成本/库存证据、审批/Shadow/Live/回滚/熔断状态。
飞书卡片包含同一建议摘要和本地 Web 回链。

- [ ] **Step 2: 实现只读模型和页面**

聚合查询只在 `ReportReadModel`，视图不得自行推导财务指标。敏感值和
Webhook 不进入 HTML。

- [ ] **Step 3: 验证并提交**

Run: `.venv/bin/python -m pytest tests/reporting tests/dashboard tests/approval/test_feishu.py -q`
Expected: PASS。

```bash
git add src/adwatch/reporting src/adwatch/dashboard src/adwatch/approval/feishu.py tests/reporting tests/dashboard tests/approval/test_feishu.py
git commit -m "feat: add ad optimization center"
```

## Task 10：紫鸟动作现场激活、写后验证与回滚

**Files:**
- Modify: `src/adwatch/execution/actions.py`
- Modify: `src/adwatch/execution/executor.py`
- Modify: `src/adwatch/execution/ziniao_backend.py`
- Modify: `src/adwatch/execution/activation.py`
- Test: `tests/execution/test_actions.py`
- Modify: `tests/execution/test_executor.py`
- Modify: `tests/execution/test_ziniao_backend.py`

- [ ] **Step 1: 写执行链失败测试**

预算、目标 ROAS、暂停和恢复必须使用独立适配器；审批过期、建议版本
变化、before 状态漂移、熔断打开、Live 关闭、目标不在精确白名单、
选择器未现场激活时均拒绝执行。

- [ ] **Step 2: 修正 Shadow 语义**

Shadow 只允许读取、填值到未提交状态、截图并记录 intended-after，
不得点击保存。Live 写后重新读取；不一致时回滚；回滚失败打开熔断。

- [ ] **Step 3: 验证并提交**

Run: `.venv/bin/python -m pytest tests/execution -q`
Expected: PASS。

```bash
git add src/adwatch/execution tests/execution
git commit -m "feat: harden ziniao ad action execution"
```

## Task 11：三日对账、策略回放和上线门禁

**Files:**
- Create: `src/adwatch/reconciliation/__init__.py`
- Create: `src/adwatch/reconciliation/service.py`
- Modify: `src/adwatch/operations/launch_checklist.py`
- Modify: `src/adwatch/cli.py`
- Test: `tests/reconciliation/test_service.py`
- Modify: `tests/operations/test_launch_checklist.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: 写失败测试**

三日对账按字段比较花费、GMV、订单、状态、退款、结算和 Campaign，
输出准确率与 `timezone/attribution/refund_lag/display_lag/unknown`
差异分类。任一天核心准确率低于 99% 时 `live_allowlist` 不得 ready。

- [ ] **Step 2: 实现策略回放**

对指定历史 Campaign 使用当时事实和规则版本重建建议，比对原建议，
保存一致性结果，不执行广告动作。

- [ ] **Step 3: 增加 CLI**

提供：

```bash
adwatch reconcile import --date YYYY-MM-DD --file platform-export.xlsx
adwatch reconcile report --from YYYY-MM-DD --to YYYY-MM-DD
adwatch strategy replay --campaign ID --from YYYY-MM-DD --to YYYY-MM-DD
adwatch launch-checklist
```

- [ ] **Step 4: 验证并提交**

Run: `.venv/bin/python -m pytest tests/reconciliation tests/operations tests/test_cli.py -q`
Expected: PASS。

```bash
git add src/adwatch/reconciliation src/adwatch/operations/launch_checklist.py src/adwatch/cli.py tests/reconciliation tests/operations/test_launch_checklist.py tests/test_cli.py
git commit -m "feat: add launch reconciliation gates"
```

## Task 12：端到端验收、文档与现场待办

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `docs/blueprint-gap-audit-2026-07-27.md`
- Modify: `task_plan.md`
- Modify: `progress.md`
- Test: `tests/test_cli_end_to_end.py`
- Test: `tests/test_daily_run_cli.py`

- [ ] **Step 1: 写端到端失败测试**

使用模拟订单、成本、采购、退款、刷单、广告和库存完成：

```text
采集 → 成本快照 → 库存移动 → 三种 ROAS → 诊断 → 建议
→ 审批 → Shadow → 报告 → 分润草稿 → 上线门禁
```

断言真实广告写入保持关闭。

- [ ] **Step 2: 更新配置和文档**

记录 Web 启动、人工录入、成本导入、日常任务、对账、策略回放、备份和
恢复命令。外部尚未完成项必须列出真实数据/权限/现场动作及完成证据，
不得标记为代码缺失。

- [ ] **Step 3: 全量验证**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures。

Run: `.venv/bin/ruff check src tests`
Expected: `All checks passed!`

Run: `.venv/bin/adwatch doctor`
Expected: 代码与本地配置项逐项显示；外部未完成项明确为
`pending_external`，不出现未捕获异常。

Run: `.venv/bin/adwatch launch-checklist`
Expected: 代码项完成；三日真实对账、现场选择器或 Live 白名单等事实项
只有在存在证据时才显示 ready。

- [ ] **Step 4: 提交**

```bash
git add README.md .env.example docs/blueprint-gap-audit-2026-07-27.md task_plan.md progress.md tests/test_cli_end_to_end.py tests/test_daily_run_cli.py
git commit -m "docs: complete unified operations launch workflow"
```

## 完成条件

- 第二至第四批所有自动测试通过，Web 可在本机完整维护人工事实；
- 广告调优使用真实经营证据，并保留三种 ROAS 和策略版本；
- 真实广告写入仍默认关闭；
- 第五批代码工具完成，但“三日 99%”“现场选择器激活”“Live 精确
  白名单”只有在真实证据完成后才能勾选；
- 所有尚需用户或平台参与的事项集中在最终上线待办，不伪装为已完成。

## 执行结果（2026-07-28）

- [x] Task 1–11 的代码、迁移、CLI、Web 路由和自动化测试已落地。
- [x] Task 12 的端到端链路、操作文档与上线待办审计已落地。
- [x] 补充完成平台订单到历史成本快照、库存移动、利润和每日分析的
  幂等同步，并修正分润按实际出库日期计入销售成本。
- [ ] 现场项目仍由 `adwatch launch-checklist` 管理：真实数据补齐、
  连续三日 99% 对账、真实页面选择器激活、Shadow/回滚演练、飞书公网
  回调和 Live 精确白名单。上述项目需要外部证据，不属于代码缺失。
