# Adwatch Gap Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐 Adwatch 完整计划中遗漏的可开发验收点，并把真实广告页面能力改成必须现场激活的安全适配器。

**Architecture:** 分析结果使用显式可信度等级；报告、看板和运维命令从 SQLite 读取统一状态。紫鸟执行按平台和动作路由到已注册适配器，选择器配置未现场激活时 Live 在任何提交前拒绝，Shadow 只计算预期结果。

**Tech Stack:** Python 3.12、SQLite、pytest、stdlib HTTP server、紫鸟 CLI/Webdriver、macOS launchd。

---

### Task 1：分析可信度和三次有界重试

**Files:**
- Modify: `src/adwatch/analytics/service.py`
- Modify: `src/adwatch/reporting/read_model.py`
- Modify: `src/adwatch/reporting/markdown.py`
- Modify: `src/adwatch/collectors/ziniao_client.py`
- Test: `tests/analytics/test_service.py`
- Test: `tests/reporting/test_markdown.py`
- Test: `tests/collectors/test_ziniao_client.py`

- [x] **Step 1: 写可信度失败测试**

```python
def test_analysis_exposes_capability_statuses_when_costs_are_missing():
    summary = AnalysisService(database).run(data_date)
    assert summary.capabilities == {
        "platform_metrics": "ready",
        "estimated_profit": "pending_data",
        "verified_profit": "pending_data",
        "inventory_safe_strategy": "pending_data",
    }
```

- [x] **Step 2: 运行测试并确认因 `capabilities` 不存在而失败**

```bash
.venv/bin/python -m pytest \
  tests/analytics/test_service.py::test_analysis_exposes_capability_statuses_when_costs_are_missing \
  -q
```

- [x] **Step 3: 实现可信度枚举和逐级计算**

在 `AnalysisSummary` 增加不可变 `capabilities: dict[str, str]`。平台有指标时
`platform_metrics=ready`；存在最小成本时 `estimated_profit=ready`；完整成本
字段经导入校验时 `verified_profit=ready`；存在库存及预计日销量时
`inventory_safe_strategy=ready`。报告按这些状态显示“估算/已验证/待补数据”。

- [x] **Step 4: 写并验证三次重试失败测试**

```python
def test_page_exec_until_stops_after_three_attempts():
    client = ZiniaoCliClient(runner=runner_that_never_becomes_ready)
    with pytest.raises(ZiniaoApiError):
        client.page_exec_until("store", "script", ready=lambda _: False)
    assert runner_that_never_becomes_ready.calls == 3
```

Run:

```bash
.venv/bin/python -m pytest tests/collectors/test_ziniao_client.py -q
```

Expected: 默认 15 次导致断言失败。

- [x] **Step 5: 将默认尝试次数改为 3 并验证**

```python
def page_exec_until(self, store_id, script, *, ready, attempts: int = 3):
    ...
```

Run:

```bash
.venv/bin/python -m pytest tests/analytics tests/reporting \
  tests/collectors/test_ziniao_client.py -q
```

Expected: PASS。

- [x] **Step 6: 提交**

```bash
git add src/adwatch/analytics/service.py \
  src/adwatch/reporting/read_model.py src/adwatch/reporting/markdown.py \
  src/adwatch/collectors/ziniao_client.py tests/analytics/test_service.py \
  tests/reporting/test_markdown.py tests/collectors/test_ziniao_client.py
git commit -m "feat: expose analysis confidence and bound bridge retries"
```

### Task 2：商品复测池策略

**Files:**
- Modify: `src/adwatch/strategy/rules.py`
- Modify: `src/adwatch/analytics/service.py`
- Test: `tests/strategy/test_rules.py`
- Test: `tests/analytics/test_service.py`

- [x] **Step 1: 写失败测试**

```python
def test_product_retest_is_capped_at_twenty_percent():
    context = StrategyContext.example(
        retest_candidate=True,
        verified_profit=True,
        inventory_verified=True,
        available_test_budget=Decimal("100"),
        current_budget=Decimal("1000"),
    )
    result = recommend(context)
    retest = next(item for item in result if item.action == "allocate_retest")
    assert retest.amount == Decimal("100")
    assert retest.amount <= context.current_budget * Decimal("0.20")


def test_product_retest_requires_verified_profit_and_inventory():
    context = StrategyContext.example(
        retest_candidate=True,
        verified_profit=False,
        inventory_verified=False,
    )
    assert all(item.action != "allocate_retest" for item in recommend(context))
```

- [x] **Step 2: 运行并确认缺少字段/动作导致失败**

```bash
.venv/bin/python -m pytest tests/strategy/test_rules.py -q
```

- [x] **Step 3: 实现最小复测建议**

扩展 `StrategyContext`：

```python
retest_candidate: bool = False
verified_profit: bool = False
inventory_verified: bool = False
available_test_budget: Decimal = Decimal(0)
```

扩展 `Recommendation` 为可选 `amount`。只有候选商品、已验证利润和库存同时
满足时生成 `allocate_retest`，金额为：

```python
min(context.available_test_budget, context.current_budget * Decimal("0.20"))
```

该动作只写建议表，不进入广告执行动作白名单。

- [x] **Step 4: 验证并提交**

```bash
.venv/bin/python -m pytest tests/strategy tests/analytics/test_service.py -q
git add src/adwatch/strategy/rules.py src/adwatch/analytics/service.py \
  tests/strategy/test_rules.py tests/analytics/test_service.py
git commit -m "feat: add guarded product retest recommendations"
```

### Task 3：日报、备份验证和运行状态 CLI

**Files:**
- Modify: `src/adwatch/cli.py`
- Modify: `src/adwatch/operations/backup.py`
- Test: `tests/test_cli.py`
- Test: `tests/operations/test_backup.py`

- [x] **Step 1: 写日报和备份验证失败测试**

```python
def test_daily_report_cli_writes_existing_date(tmp_path, monkeypatch):
    assert main(["report", "daily", "--date", "2026-07-23"]) == 0
    assert (tmp_path / "reports" / "daily-2026-07-23.md").exists()


def test_backup_verify_cli_rejects_corrupt_database(tmp_path, monkeypatch):
    path = tmp_path / "broken.sqlite3"
    path.write_bytes(b"not sqlite")
    assert main(["backup", "verify", "--path", str(path)]) == 2
```

- [x] **Step 2: 运行并确认子命令不存在**

```bash
.venv/bin/python -m pytest tests/test_cli.py \
  tests/operations/test_backup.py -q
```

- [x] **Step 3: 实现命令**

新增 parser：

```python
daily_report = report_commands.add_parser("daily")
daily_report.add_argument("--date", type=date.fromisoformat, required=True)
backup_verify = backup_commands.add_parser("verify")
backup_verify.add_argument("--path", type=Path, required=True)
```

日报使用 `ReportReadModel.daily` 和 `render_daily_markdown`。备份验证捕获
`sqlite3.DatabaseError`，输出明确失败并返回 2。

- [x] **Step 4: 验证并提交**

```bash
.venv/bin/python -m pytest tests/test_cli.py tests/operations -q
git add src/adwatch/cli.py src/adwatch/operations/backup.py \
  tests/test_cli.py tests/operations/test_backup.py
git commit -m "feat: add daily report and backup verification commands"
```

### Task 4：看板趋势、质量、审批与执行状态

**Files:**
- Modify: `src/adwatch/reporting/read_model.py`
- Modify: `src/adwatch/dashboard/app.py`
- Test: `tests/reporting/test_read_model.py`
- Test: `tests/dashboard/test_app.py`

- [x] **Step 1: 写读取模型失败测试**

```python
def test_dashboard_read_model_includes_trends_and_operations(database):
    model = ReportReadModel(database)
    snapshot = model.dashboard(date(2026, 7, 23))
    assert set(snapshot.trends) == {7, 14, 30}
    assert snapshot.collection_runs
    assert snapshot.approval_counts["approved"] == 1
    assert snapshot.execution_counts["succeeded"] == 1
```

- [x] **Step 2: 运行并确认 `dashboard` 读取接口不存在**

```bash
.venv/bin/python -m pytest tests/reporting/test_read_model.py -q
```

- [x] **Step 3: 实现聚合读取模型**

新增 `DashboardSnapshot`，包含：

```python
daily: DailySnapshot
trends: dict[int, tuple[TrendPoint, ...]]
collection_runs: tuple[dict, ...]
approval_counts: dict[str, int]
execution_counts: dict[str, int]
```

所有查询只读 SQLite，并受平台/店铺/Campaign/SKU 参数约束。

- [x] **Step 4: 写 HTML 失败测试并实现视图**

```python
def test_dashboard_renders_trends_quality_and_execution(database):
    page = render_dashboard(database, data_date, simulated=False)
    assert "7 天趋势" in page
    assert "14 天趋势" in page
    assert "30 天趋势" in page
    assert "采集运行质量" in page
    assert "审批与执行状态" in page
```

使用无外部依赖的表格/迷你 SVG 折线图；无数据时显示“暂无数据”，不得
伪造零值。

- [x] **Step 5: 验证并提交**

```bash
.venv/bin/python -m pytest tests/reporting tests/dashboard -q
git add src/adwatch/reporting/read_model.py src/adwatch/dashboard/app.py \
  tests/reporting/test_read_model.py tests/dashboard/test_app.py
git commit -m "feat: add operational trends to local dashboard"
```

### Task 5：平台动作适配器与现场激活门禁

**Files:**
- Create: `src/adwatch/execution/actions.py`
- Create: `src/adwatch/execution/activation.py`
- Modify: `src/adwatch/execution/ziniao_backend.py`
- Modify: `src/adwatch/execution/executor.py`
- Modify: `src/adwatch/collectors/ziniao_client.py`
- Modify: `src/adwatch/storage/migrations.py`
- Modify: `src/adwatch/cli.py`
- Test: `tests/execution/test_actions.py`
- Test: `tests/execution/test_activation.py`
- Modify: `tests/execution/test_ziniao_backend.py`
- Modify: `tests/execution/test_executor.py`
- Modify: `tests/collectors/test_ziniao_client.py`

- [x] **Step 1: 写动作注册表失败测试**

```python
def test_each_platform_action_has_a_dedicated_adapter():
    registry = ActionRegistry.default()
    for platform in ("tiktok", "shopee"):
        for action in (
            "increase_budget", "reduce_budget", "adjust_roas_target",
            "pause", "resume",
        ):
            assert registry.get(platform, action) is not None


def test_action_adapter_does_not_accept_arbitrary_script():
    assert "script" not in inspect.signature(ActionAdapter.execute).parameters
```

- [x] **Step 2: 运行并确认模块不存在**

```bash
.venv/bin/python -m pytest tests/execution/test_actions.py -q
```

- [x] **Step 3: 实现专用动作接口**

```python
class ActionAdapter(Protocol):
    def read(self, client, store_id, campaign_id, selectors) -> dict: ...
    def stage(self, client, store_id, campaign_id, intended, selectors) -> None: ...
    def submit(self, client, store_id, campaign_id, selectors) -> None: ...
    def capture(self, client, store_id, label) -> str: ...
```

注册表按 `(platform, action)` 返回固定实现。JavaScript 仅存在于各实现内部，
调用方不能传入脚本。Shadow 只调用 `read` 并计算 intended，不调用
`stage/submit`。

- [x] **Step 4: 写现场激活失败测试**

```python
def test_live_rejects_inactive_selector_before_stage(database):
    activation = SelectorActivationStore(database)
    backend = ZiniaoExecutionBackend(
        client, mode="live", policy=live_policy,
        activations=activation, registry=registry,
    )
    with pytest.raises(PolicyError, match="not field-activated"):
        backend.execute(recommendation)
    assert client.calls == []
```

- [x] **Step 5: 增加迁移和激活存储**

新增 `selector_activations`：

```sql
CREATE TABLE selector_activations (
  platform TEXT NOT NULL,
  action TEXT NOT NULL,
  selector_version TEXT NOT NULL,
  selectors_json TEXT NOT NULL,
  store_id TEXT NOT NULL,
  activated_by TEXT NOT NULL,
  evidence_before TEXT NOT NULL,
  evidence_after TEXT NOT NULL,
  activated_at TEXT NOT NULL,
  PRIMARY KEY(platform, action)
);
```

CLI 只提供显式现场登记：

```text
adwatch activation list
adwatch activation register --platform ... --action ... --version ...
  --store-id ... --selectors-file ... --activated-by ...
  --evidence-before ... --evidence-after ...
```

登记动作不自动开启 Live。

- [x] **Step 6: 改造 Backend 和截图**

Backend 先执行 Policy，再读取激活配置。Live 未激活直接拒绝；已激活时依次
调用 `read/stage/submit/read`。`ZiniaoCliClient.page_screenshot` 只接受
`store_id` 和经过 `Path.resolve()` 后仍位于 `var/screenshots` 的目标路径，
内部调用固定的紫鸟截图命令；审计表保存真实文件路径。失败后使用同一适配器
恢复旧值。

- [x] **Step 7: 验证并提交**

```bash
.venv/bin/python -m pytest tests/execution tests/test_cli.py \
  tests/storage -q
git add src/adwatch/execution src/adwatch/collectors/ziniao_client.py \
  src/adwatch/storage/migrations.py src/adwatch/cli.py tests/execution \
  tests/collectors/test_ziniao_client.py tests/storage tests/test_cli.py
git commit -m "feat: require field activation for Ziniao live actions"
```

### Task 6：完整上线清单、代码质量和最终证据

**Files:**
- Modify: `src/adwatch/operations/launch_checklist.py`
- Modify: `src/adwatch/operations/readiness.py`
- Modify: `src/adwatch/cli.py`
- Modify: `src/adwatch/dashboard/app.py`
- Modify: `src/adwatch/__main__.py`
- Modify: `src/adwatch/pipeline/validation.py`
- Modify: `tests/pipeline/test_validation.py`
- Modify: `tests/operations/test_launch_checklist.py`
- Modify: `tests/operations/test_readiness.py`
- Modify: `docs/blueprint-gap-audit-2026-07-27.md`
- Modify: `README.md`

- [x] **Step 1: 写完整清单失败测试**

```python
def test_launch_checklist_includes_every_external_gate():
    readiness = LaunchReadiness(
        ziniao_bridge=False,
        tiktok_campaign_validation=False,
        shopee_campaign_validation=False,
        business_costs=False,
        sku_mapping=False,
        refund_source=False,
        inventory_source=False,
        exchange_rate_source=False,
        feishu_callback=False,
        shadow_reconciliation=False,
        rollback_drill=False,
        selector_activation=False,
        platform_api_oauth=False,
        three_day_reconciliation=False,
        live_allowlist=False,
    )
    items = build_launch_checklist(readiness)
    assert {item.code for item in items} == {
        "ziniao_bridge", "tiktok_campaign_validation",
        "shopee_campaign_validation", "business_costs", "sku_mapping",
        "refund_source", "inventory_source", "exchange_rate_source",
        "feishu_callback", "shadow_reconciliation", "rollback_drill",
        "selector_activation", "platform_api_oauth",
        "three_day_reconciliation", "live_allowlist",
    }
```

- [x] **Step 2: 运行并确认清单缺项**

```bash
.venv/bin/python -m pytest tests/operations/test_launch_checklist.py -q
```

- [x] **Step 3: 实现清单并修复计划指定 Lint**

清单状态从 SQLite、Settings 和 Bridge 健康检查读取。`platform_api_oauth`
标记为可选，但在 JSON 中仍显示 `pending_external`。

修复：

```bash
.venv/bin/ruff check --select E,F,I src tests --fix
```

仅对剩余超长 HTML 行手工换行，不运行其他规则的批量修复。

- [x] **Step 4: 更新审计与 README**

审计按 `code_ready` 和 `field_activated` 两列报告；README 记录所有新命令、
选择器激活流程和 Live 永不自动开启的规则。

- [x] **Step 5: 全量验证**

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check --select E,F,I src tests
.venv/bin/python -m compileall -q src/adwatch tests
.venv/bin/adwatch report --help
.venv/bin/adwatch backup --help
.venv/bin/adwatch activation --help
.venv/bin/adwatch launch-checklist --format markdown
test "$(grep '^ADWATCH_LIVE_WRITES=' .env)" = \
  "ADWATCH_LIVE_WRITES=false"
```

Expected: pytest、Ruff、compileall 全部退出 0；命令齐全；清单只剩真实外部
条件；Live 为 false。

- [x] **Step 6: 提交**

```bash
git add src tests README.md docs/blueprint-gap-audit-2026-07-27.md
git commit -m "feat: close adwatch implementation gaps"
```
