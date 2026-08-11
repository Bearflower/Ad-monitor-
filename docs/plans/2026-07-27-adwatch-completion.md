# Adwatch Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成蓝图 v1.2 中所有可开发和可影子验收的功能，同时保持真实广告写操作默认关闭，并在最后自动生成上线待办清单。

**Architecture:** 紫鸟 CLI 继续承担双平台页面读取与 P2 页面执行。数据、分析、审批和执行通过 SQLite 解耦；运行状态使用 `ready/pending_data/pending_external/blocked` 明确表示。执行分为 read-only、shadow、live，live 需要总开关、允许清单、有效审批和关闭的熔断器。

**Tech Stack:** Python 3.12、SQLite、pytest、stdlib HTTP server、紫鸟 CLI/Webdriver、飞书 Webhook/事件回调、macOS launchd。

---

## 实施批次

本计划按依赖顺序执行。每个 Task 独立提交，所有真实广告写操作在整个开发期保持关闭。

### Task 1：简化经营输入并引入分析可信度

**Files:**
- Modify: `src/adwatch/analytics/business_inputs.py`
- Modify: `src/adwatch/analytics/service.py`
- Modify: `src/adwatch/storage/migrations.py`
- Modify: `src/adwatch/reporting/read_model.py`
- Modify: `src/adwatch/reporting/markdown.py`
- Modify: `src/adwatch/cli.py`
- Test: `tests/analytics/test_business_inputs.py`
- Test: `tests/analytics/test_service.py`
- Test: `tests/reporting/test_markdown.py`
- Test: `tests/test_business_cli.py`

- [ ] **Step 1: 写失败测试**

覆盖：

```python
def test_minimal_cost_csv_requires_only_date_product_cost_and_optional_refund():
    ...

def test_missing_cost_keeps_platform_analysis_but_marks_profit_pending_data():
    ...

def test_missing_inventory_blocks_only_increase_budget_not_daily_report():
    ...
```

- [ ] **Step 2: 验证测试失败**

Run:

```bash
.venv/bin/python -m pytest tests/analytics/test_business_inputs.py tests/analytics/test_service.py tests/reporting/test_markdown.py tests/test_business_cli.py -q
```

Expected: 新测试因最小 CSV 和可信度状态尚不存在而失败。

- [ ] **Step 3: 实现分层数据状态**

新增 `analysis_status`：

```text
platform_metrics
estimated_profit
verified_profit
inventory_safe_strategy
```

将每日 CSV 缩减为：

```text
data_date,total_product_cost,refund_amount
```

Campaign 一次性配置单独导入；运费、优惠、固定成本默认 0，但报告必须标记为估算利润。

- [ ] **Step 4: 验证**

Run:

```bash
.venv/bin/python -m pytest tests/analytics tests/reporting tests/test_business_cli.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/adwatch/analytics src/adwatch/storage/migrations.py src/adwatch/reporting src/adwatch/cli.py tests/analytics tests/reporting tests/test_business_cli.py
git commit -m "feat: simplify business inputs and grade analysis confidence"
```

### Task 2：可靠双平台采集与单平台故障隔离

**Files:**
- Modify: `src/adwatch/collectors/ziniao.py`
- Modify: `src/adwatch/collectors/ziniao_client.py`
- Modify: `src/adwatch/pipeline/runner.py`
- Modify: `src/adwatch/cli.py`
- Create: `src/adwatch/pipeline/daily.py`
- Test: `tests/collectors/test_ziniao.py`
- Test: `tests/collectors/test_ziniao_client.py`
- Test: `tests/pipeline/test_daily.py`
- Test: `tests/test_daily_run_cli.py`

- [ ] **Step 1: 写失败测试**

```python
def test_tiktok_campaign_rows_are_parsed_into_daily_metrics():
    ...

def test_shopee_still_collects_when_tiktok_fails():
    ...

def test_bridge_retry_is_bounded_and_failure_is_recorded():
    ...
```

- [ ] **Step 2: 验证测试失败**

Run:

```bash
.venv/bin/python -m pytest tests/collectors tests/pipeline/test_daily.py tests/test_daily_run_cli.py -q
```

- [ ] **Step 3: 实现**

TikTok 解析 Spend、GMV、Orders、Campaign ID；Shopee 保留 Campaign 汇总并支持 SKU 分页结果。每日编排逐平台捕获错误，不让一个平台阻塞另一个平台。Bridge 重试采用固定上限 3 次，并写入 `collection_runs`。

- [ ] **Step 4: 验证并提交**

```bash
.venv/bin/python -m pytest tests/collectors tests/pipeline tests/test_daily_run_cli.py -q
git add src/adwatch/collectors src/adwatch/pipeline src/adwatch/cli.py tests/collectors tests/pipeline tests/test_daily_run_cli.py
git commit -m "feat: make dual-platform collection resilient"
```

### Task 3：时间窗口、异常与熔断主流程联动

**Files:**
- Modify: `src/adwatch/analytics/windows.py`
- Modify: `src/adwatch/analytics/anomalies.py`
- Modify: `src/adwatch/analytics/service.py`
- Modify: `src/adwatch/strategy/circuit_breaker.py`
- Modify: `src/adwatch/storage/analytics.py`
- Test: `tests/analytics/test_windows.py`
- Test: `tests/analytics/test_anomalies.py`
- Test: `tests/analytics/test_service.py`
- Test: `tests/strategy/test_circuit_breaker.py`

- [ ] **Step 1: 写失败测试**

覆盖 7/14/30 天基线、花费突增、ROAS 暴跌、学习期中断、库存风险、3 次 Webdriver 失败和连续 2 天全局低 ROAS。

- [ ] **Step 2: 运行并确认失败**

```bash
.venv/bin/python -m pytest tests/analytics tests/strategy/test_circuit_breaker.py -q
```

- [ ] **Step 3: 接入主流程**

从 SQLite 真实历史计算窗口，不再给 `webdriver_failures` 和 `consecutive_global_low_roas_days` 传固定 0。缺成本只产生 `pending_data`，不作为数据质量熔断；关键采集字段缺失仍触发熔断。

- [ ] **Step 4: 验证并提交**

```bash
.venv/bin/python -m pytest tests/analytics tests/strategy -q
git add src/adwatch/analytics src/adwatch/strategy src/adwatch/storage/analytics.py tests/analytics tests/strategy
git commit -m "feat: wire trend anomalies into circuit decisions"
```

### Task 4：补齐三板斧策略

**Files:**
- Modify: `src/adwatch/strategy/rules.py`
- Modify: `src/adwatch/analytics/service.py`
- Test: `tests/strategy/test_rules.py`
- Test: `tests/analytics/test_service.py`

- [ ] **Step 1: 写失败测试**

```python
def test_recommend_roas_target_adjustment_only_after_learning():
    ...

def test_pause_requires_three_real_consecutive_low_days():
    ...

def test_increase_budget_requires_verified_profit_and_inventory():
    ...

def test_product_retest_never_exceeds_twenty_percent_pool():
    ...
```

- [ ] **Step 2: 实现 ROAS 校准、预算分配、暂停和选品建议**

所有变更均输出确定性 `change_ratio`、原因、数据窗口和所需审批级别。缺库存时永不生成加预算建议。

- [ ] **Step 3: 验证并提交**

```bash
.venv/bin/python -m pytest tests/strategy tests/analytics/test_service.py -q
git add src/adwatch/strategy src/adwatch/analytics/service.py tests/strategy tests/analytics/test_service.py
git commit -m "feat: complete guarded three-lever strategy"
```

### Task 5：报告、看板、备份与 launchd 可靠性

**Files:**
- Modify: `src/adwatch/reporting/markdown.py`
- Modify: `src/adwatch/reporting/read_model.py`
- Modify: `src/adwatch/dashboard/app.py`
- Create: `src/adwatch/operations/backup.py`
- Create: `src/adwatch/operations/readiness.py`
- Modify: `src/adwatch/cli.py`
- Test: `tests/reporting/test_markdown.py`
- Test: `tests/dashboard/test_app.py`
- Create: `tests/operations/test_backup.py`
- Create: `tests/operations/test_readiness.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: 写失败测试**

覆盖周报/月报 CLI、平台/店铺/Campaign/SKU 筛选、7/14/30 天趋势、运行质量、审批执行状态、SQLite 在线备份验证和 Bridge 未启动时的明确通知。

- [ ] **Step 2: 实现**

新增：

```text
adwatch report daily|weekly|monthly
adwatch backup create|verify
adwatch readiness
```

launchd 前置检查失败时保存本地状态并发送飞书故障通知，不生成 `daily_run=ok`。

- [ ] **Step 3: 验证并提交**

```bash
.venv/bin/python -m pytest tests/reporting tests/dashboard tests/operations tests/test_cli.py -q
git add src/adwatch/reporting src/adwatch/dashboard src/adwatch/operations src/adwatch/cli.py tests/reporting tests/dashboard tests/operations tests/test_cli.py
git commit -m "feat: complete reporting dashboard and local operations"
```

### Task 6：飞书签名回调服务

**Files:**
- Modify: `src/adwatch/approval/feishu.py`
- Create: `src/adwatch/approval/server.py`
- Modify: `src/adwatch/config.py`
- Modify: `src/adwatch/cli.py`
- Test: `tests/approval/test_feishu.py`
- Create: `tests/approval/test_server.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: 写失败测试**

覆盖 challenge 校验、签名验证、时间戳过期、重复事件、批准/拒绝和非法 payload。

- [ ] **Step 2: 实现本地回调服务**

```text
adwatch approval serve --host 127.0.0.1 --port 8787
```

本地测试可完成代码验收；没有公网 HTTPS 时 readiness 返回 `pending_external`。

- [ ] **Step 3: 验证并提交**

```bash
.venv/bin/python -m pytest tests/approval tests/test_config.py -q
git add src/adwatch/approval src/adwatch/config.py src/adwatch/cli.py tests/approval tests/test_config.py
git commit -m "feat: add signed Feishu approval callback service"
```

### Task 7：紫鸟 CLI Shadow/Live 执行 Backend

**Files:**
- Create: `src/adwatch/execution/policy.py`
- Create: `src/adwatch/execution/ziniao_backend.py`
- Modify: `src/adwatch/execution/executor.py`
- Modify: `src/adwatch/config.py`
- Modify: `src/adwatch/cli.py`
- Test: `tests/execution/test_policy.py`
- Create: `tests/execution/test_ziniao_backend.py`
- Test: `tests/execution/test_executor.py`

- [ ] **Step 1: 写失败测试**

覆盖：

```python
def test_shadow_reads_and_records_but_never_submits():
    ...

def test_live_is_disabled_by_default():
    ...

def test_live_requires_store_and_campaign_allowlist():
    ...

def test_before_value_drift_aborts_execution():
    ...

def test_delete_and_account_actions_are_permanently_blocked():
    ...
```

- [ ] **Step 2: 实现策略和 Backend**

Shadow Backend 只允许读值、填充到非提交状态、截图和记录 intended-after。Live Backend 只有 `ADWATCH_LIVE_WRITES=true` 且目标在允许清单中才可点击保存。预算、ROAS、暂停/恢复使用独立页面动作；禁止通用任意 JavaScript 写入入口。

- [ ] **Step 3: 实现补偿回滚**

写后验证失败时，仅对已经确认可逆的预算、ROAS、暂停/恢复执行恢复旧值。回滚失败打开全局熔断并发送 critical 告警。

- [ ] **Step 4: 验证并提交**

```bash
.venv/bin/python -m pytest tests/execution tests/test_config.py -q
git add src/adwatch/execution src/adwatch/config.py src/adwatch/cli.py tests/execution tests/test_config.py
git commit -m "feat: add guarded Ziniao shadow and live execution"
```

### Task 8：外部数据适配器合同与上线待办

**Files:**
- Create: `src/adwatch/integrations/refunds.py`
- Create: `src/adwatch/integrations/inventory.py`
- Create: `src/adwatch/integrations/exchange_rates.py`
- Create: `src/adwatch/integrations/platform_api.py`
- Create: `src/adwatch/operations/launch_checklist.py`
- Modify: `src/adwatch/cli.py`
- Create: `tests/integrations/test_contracts.py`
- Create: `tests/operations/test_launch_checklist.py`

- [ ] **Step 1: 写失败测试**

验证模拟退款 T+3 回溯、库存快照、汇率更新、TikTok/Shopee API 未授权状态，以及待办清单按真实状态消项。

- [ ] **Step 2: 实现可替换合同和模拟实现**

官方 API 权限缺失时返回 `pending_external`；不得返回空成功。紫鸟 CLI 主线不依赖这些 API Adapter。

- [ ] **Step 3: 实现统一清单**

```text
adwatch launch-checklist --format markdown
adwatch launch-checklist --format json
```

清单包含经营 CSV、TikTok 有数据验收、Bridge 常驻、飞书公网回调、Shadow 对账、Live 允许清单、回滚演练和可选 API OAuth。

- [ ] **Step 4: 全量验证**

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check --select E,F,I src tests
.venv/bin/adwatch launch-checklist --format markdown
```

Expected: 全部测试通过；清单只保留真实外部条件和数据待办。

- [ ] **Step 5: 最终提交**

```bash
git add src/adwatch/integrations src/adwatch/operations src/adwatch/cli.py tests/integrations tests/operations
git commit -m "feat: add external adapters and launch readiness checklist"
```

## 完成后的真实上线顺序

1. 填写极简经营 CSV。
2. 启动紫鸟客户端并验证 Bridge。
3. 使用有数据的 TikTok Campaign 现场校验。
4. 配置飞书公网 HTTPS 回调。
5. 连续运行 Shadow 模式并人工对账。
6. 完成预算、ROAS、暂停/恢复的回滚演练。
7. 逐店铺加入 Live 允许清单。
8. 最后单独开启 `ADWATCH_LIVE_WRITES=true`。
