# Adwatch Analytics and Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic profit calculations, rolling performance analysis, anomaly detection, learning-period-safe recommendations, and write-operation circuit breakers to the working data foundation.

**Architecture:** Versioned migrations add business inputs and derived outputs without changing collected raw metrics. Pure calculation functions handle money and strategy rules; repositories only load and persist records. An analysis service processes one date at a time and can be safely rerun because derived tables have stable unique keys.

**Tech Stack:** Python 3.12, standard-library `decimal`, `sqlite3`, `statistics`, existing Adwatch package, pytest, Ruff.

---

## File map

- `src/adwatch/storage/migrations.py`: add stores, campaigns, SKU mappings, costs, inventory, exchange rates, profit results, alerts, recommendations, settings, and circuit state.
- `src/adwatch/analytics/profit.py`: six-layer profit formula using Decimal.
- `src/adwatch/analytics/windows.py`: 7/14/30-day aggregates and consecutive-day predicates.
- `src/adwatch/analytics/anomalies.py`: spend, ROAS, inventory, and data-quality anomaly rules.
- `src/adwatch/analytics/service.py`: load metrics and business inputs, calculate, and persist results.
- `src/adwatch/strategy/rules.py`: learning-period and budget/ROAS/pause recommendations.
- `src/adwatch/strategy/circuit_breaker.py`: trigger and recover write-operation suspension.
- `src/adwatch/storage/analytics.py`: business-input and derived-output repository.
- `src/adwatch/cli.py`: `analyze`, `seed-business-data`, and circuit inspection commands.
- `tests/analytics/`, `tests/strategy/`, `tests/storage/`: focused and integration tests.

### Task 1: Business and analysis schema

**Files:**
- Modify: `src/adwatch/storage/migrations.py`
- Test: `tests/storage/test_analytics_migration.py`

- [ ] **Step 1: Write the failing migration test**

```python
from adwatch.storage.db import Database


def test_v2_migration_adds_analysis_tables(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    db.migrate()
    with db.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "stores", "campaign_settings", "sku_mappings", "product_costs",
        "inventory_snapshots", "exchange_rates", "profit_results",
        "alerts", "recommendations", "system_settings", "circuit_state"
    } <= tables
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest tests/storage/test_analytics_migration.py -q`

Expected: FAIL because the tables are absent.

- [ ] **Step 3: Add migration version 2**

Create the asserted tables. Store all money and rates as decimal text. Required stable keys:

- costs: `(sku_id, effective_date)`
- inventory: `(sku_id, snapshot_date)`
- exchange rates: `(currency, rate_date)`
- profits: the daily metric logical key
- alerts: `(rule_code, platform, campaign_id, sku_id, data_date)`
- recommendations: `(rule_code, platform, campaign_id, sku_id, data_date)`
- circuit state: singleton row with id `1`

Seed `system_settings` using `INSERT OR IGNORE` with:

```text
tiktok_learning_days=7
shopee_learning_days=14
learning_budget_change_limit=0.20
normal_budget_change_limit=0.30
pause_roas_ratio=0.50
reduce_roas_ratio=0.70
high_roas_ratio=1.00
alert_daily_circuit_count=5
webdriver_failure_circuit_count=3
global_roas_circuit_ratio=0.60
```

- [ ] **Step 4: Run all storage tests**

Run: `.venv/bin/python -m pytest tests/storage -q`

Expected: all storage tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/adwatch/storage/migrations.py tests/storage/test_analytics_migration.py
git commit -m "feat: add analytics schema"
```

### Task 2: Six-layer profit calculation

**Files:**
- Create: `src/adwatch/analytics/__init__.py`
- Create: `src/adwatch/analytics/profit.py`
- Test: `tests/analytics/test_profit.py`

- [ ] **Step 1: Write failing formula tests**

```python
from decimal import Decimal

from adwatch.analytics.profit import ProfitInput, calculate_profit


def test_profit_uses_refunds_commission_costs_and_operating_deductions():
    result = calculate_profit(ProfitInput(
        gmv=Decimal("1000"), refunds=Decimal("100"),
        commission_rate=Decimal("0.08"), product_cost=Decimal("300"),
        ad_spend=Decimal("120"), seller_shipping=Decimal("40"),
        coupons=Decimal("20"), allocated_fixed_cost=Decimal("30"),
        exchange_rate_to_cny=Decimal("1.50"),
    ))
    assert result.net_sales_cny == Decimal("1350.00")
    assert result.platform_commission_cny == Decimal("108.00")
    assert result.gross_profit_cny == Decimal("792.00")
    assert result.net_profit_cny == Decimal("477.00")


def test_zero_spend_has_no_break_even_roas():
    result = calculate_profit(ProfitInput.zero())
    assert result.break_even_roas is None
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest tests/analytics/test_profit.py -q`

Expected: FAIL because profit functions do not exist.

- [ ] **Step 3: Implement pure Decimal calculations**

Define frozen `ProfitInput` and `ProfitResult`. Convert all local-currency amounts to CNY with the supplied exchange rate, quantize money to `0.01` and ratios to `0.0001`. Commission is `net_sales * commission_rate`. Gross profit is net sales less commission and product cost. Net profit additionally deducts ad spend, seller shipping, coupons, and fixed cost. Break-even ROAS is the GMV needed to cover non-ad costs divided by ad spend; return `None` when ad spend is zero.

- [ ] **Step 4: Run tests and lint**

Run: `.venv/bin/python -m pytest tests/analytics/test_profit.py -q`

Run: `.venv/bin/python -m ruff check src/adwatch/analytics tests/analytics`

Expected: tests pass and lint is clean.

- [ ] **Step 5: Commit**

```bash
git add src/adwatch/analytics tests/analytics/test_profit.py
git commit -m "feat: calculate six-layer profit"
```

### Task 3: Rolling windows and anomaly rules

**Files:**
- Create: `src/adwatch/analytics/windows.py`
- Create: `src/adwatch/analytics/anomalies.py`
- Test: `tests/analytics/test_windows.py`
- Test: `tests/analytics/test_anomalies.py`

- [ ] **Step 1: Write failing rolling-window tests**

```python
from datetime import date, timedelta
from decimal import Decimal

from adwatch.analytics.windows import DailyPoint, summarize_window


def test_window_uses_weighted_roas():
    end = date(2026, 7, 22)
    points = [
        DailyPoint(end - timedelta(days=1), Decimal("100"), Decimal("200")),
        DailyPoint(end, Decimal("300"), Decimal("300")),
    ]
    result = summarize_window(points, end=end, days=7)
    assert result.spend == Decimal("400.00")
    assert result.gmv == Decimal("500.00")
    assert result.roas == Decimal("1.2500")
```

- [ ] **Step 2: Write failing anomaly tests**

```python
from decimal import Decimal

from adwatch.analytics.anomalies import detect_anomalies


def test_spend_jump_and_roas_drop_are_detected():
    codes = {
        item.code for item in detect_anomalies(
            current_spend=Decimal("150"), baseline_spend=Decimal("100"),
            current_roas=Decimal("1.5"), baseline_roas=Decimal("2.0"),
            inventory_units=50, expected_daily_units=5,
        )
    }
    assert codes == {"spend_jump", "roas_drop"}
```

- [ ] **Step 3: Run and verify failures**

Run: `.venv/bin/python -m pytest tests/analytics/test_windows.py tests/analytics/test_anomalies.py -q`

Expected: FAIL because modules are absent.

- [ ] **Step 4: Implement windows and rules**

`DailyPoint` contains date, spend, and GMV. `summarize_window` includes dates from `end - (days - 1)` through `end` and computes weighted ROAS from summed values.

`detect_anomalies` returns immutable anomaly values for:

- spend increase over 30% from a positive baseline
- ROAS drop over 20% from a positive baseline
- inventory cover below 7 days when expected daily units is positive

Do not emit an anomaly when the necessary baseline is zero or missing.

- [ ] **Step 5: Run and commit**

Run: `.venv/bin/python -m pytest tests/analytics -q`

Expected: all analytics tests pass.

```bash
git add src/adwatch/analytics tests/analytics
git commit -m "feat: add performance windows and anomalies"
```

### Task 4: Learning-safe strategy recommendations

**Files:**
- Create: `src/adwatch/strategy/__init__.py`
- Create: `src/adwatch/strategy/rules.py`
- Test: `tests/strategy/test_rules.py`

- [ ] **Step 1: Write failing strategy tests**

```python
from datetime import date
from decimal import Decimal

from adwatch.strategy.rules import StrategyContext, recommend


def test_learning_campaign_never_receives_pause_action():
    result = recommend(StrategyContext.example(
        platform="tiktok", campaign_start=date(2026, 7, 18),
        data_date=date(2026, 7, 22), consecutive_low_days=4,
        roas=Decimal("0.2"), target_roas=Decimal("2.0"),
    ))
    assert all(item.action != "pause" for item in result)


def test_three_low_days_after_learning_recommends_pause():
    result = recommend(StrategyContext.example(
        platform="shopee", campaign_start=date(2026, 7, 1),
        data_date=date(2026, 7, 22), consecutive_low_days=3,
        roas=Decimal("0.8"), target_roas=Decimal("2.0"),
    ))
    assert [(item.action, item.requires_approval) for item in result] == [
        ("pause", True)
    ]


def test_negative_profit_or_stock_risk_blocks_budget_increase():
    context = StrategyContext.example(
        roas=Decimal("4"), target_roas=Decimal("2"),
        net_profit=Decimal("-1"), inventory_cover_days=3,
    )
    assert all(item.action != "increase_budget" for item in recommend(context))
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest tests/strategy/test_rules.py -q`

Expected: FAIL because strategy rules do not exist.

- [ ] **Step 3: Implement explicit strategy contexts**

Define frozen `StrategyContext` and `Recommendation`. Apply rules in priority order:

1. Learning period: never pause; if a budget recommendation exists, cap absolute change at 20%.
2. After learning, three consecutive days below 50% of target: pause.
3. Below 70% of target: reduce budget 30%.
4. At or above target with positive profit and inventory cover at least 14 days: increase budget 30%, capped at twice baseline budget.
5. Every write action has `requires_approval=True`.

Return at most one budget/pause action per context to avoid contradictory advice.

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/python -m pytest tests/strategy -q`

Expected: all strategy tests pass.

```bash
git add src/adwatch/strategy tests/strategy
git commit -m "feat: add learning-safe strategy rules"
```

### Task 5: Circuit breaker

**Files:**
- Create: `src/adwatch/strategy/circuit_breaker.py`
- Test: `tests/strategy/test_circuit_breaker.py`

- [ ] **Step 1: Write failing breaker tests**

```python
from adwatch.strategy.circuit_breaker import CircuitInputs, evaluate_circuit


def test_five_daily_alerts_open_the_circuit():
    result = evaluate_circuit(CircuitInputs(
        daily_alerts=5, webdriver_failures=0,
        quality_ok=True, consecutive_global_low_roas_days=0,
    ))
    assert result.is_open is True
    assert result.reasons == ("daily_alert_limit",)


def test_healthy_inputs_leave_circuit_closed():
    result = evaluate_circuit(CircuitInputs.healthy())
    assert result.is_open is False
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest tests/strategy/test_circuit_breaker.py -q`

Expected: FAIL because the breaker does not exist.

- [ ] **Step 3: Implement deterministic breaker evaluation**

Open the circuit for daily alerts `>=5`, Webdriver failures `>=3`, failed core quality checks, or global low-ROAS days `>=2`. Return every active reason in the listed stable order. Closing an already persisted circuit is not automatic; recovery is a separate repository operation requiring operator and reason.

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/python -m pytest tests/strategy -q`

Expected: all strategy tests pass.

```bash
git add src/adwatch/strategy/circuit_breaker.py tests/strategy/test_circuit_breaker.py
git commit -m "feat: add write-operation circuit breaker"
```

### Task 6: Analysis repository, service, and CLI

**Files:**
- Create: `src/adwatch/storage/analytics.py`
- Create: `src/adwatch/analytics/service.py`
- Modify: `src/adwatch/cli.py`
- Test: `tests/analytics/test_service.py`
- Test: `tests/test_analyze_cli.py`

- [ ] **Step 1: Write a failing service integration test**

```python
from datetime import date

from adwatch.analytics.service import AnalysisService
from adwatch.storage.db import Database


def test_analysis_is_idempotent_for_one_date(seeded_database):
    service = AnalysisService(seeded_database)
    first = service.run(date(2026, 7, 22))
    second = service.run(date(2026, 7, 22))
    assert first.metrics_processed == 8
    assert second.metrics_processed == 8
    with seeded_database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM profit_results WHERE data_date='2026-07-22'"
        ).fetchone()[0]
    assert count == 8
```

- [ ] **Step 2: Add a failing CLI test**

```python
def test_analyze_command_prints_summary(seeded_data_dir, monkeypatch, capsys):
    monkeypatch.setenv("ADWATCH_DATA_DIR", str(seeded_data_dir))
    assert main(["analyze", "--date", "2026-07-22"]) == 0
    assert "profit_results=8" in capsys.readouterr().out
```

- [ ] **Step 3: Run and verify failures**

Run: `.venv/bin/python -m pytest tests/analytics/test_service.py tests/test_analyze_cli.py -q`

Expected: FAIL because the service and command are absent.

- [ ] **Step 4: Implement repository and service**

The repository loads each daily metric with the most recent effective cost, same-date inventory, and same-date exchange rate. Missing cost/inventory/rate creates a `missing_business_input` alert and skips profit/recommendation generation for that metric.

The service upserts profit results, anomaly alerts, recommendations, and circuit state in one transaction. It returns counts for metrics processed, profit results, alerts, and recommendations. Reruns update stable keys.

Add `seed-business-data --date` for mock mode. It creates deterministic costs, inventory, campaign targets/start dates, and exchange rates for every collected mock SKU/currency. Add `analyze --date`.

- [ ] **Step 5: Run end-to-end analysis**

Run:

```bash
analysis_dir=$(mktemp -d /tmp/adwatch-analysis.XXXXXX)
ADWATCH_DATA_DIR="$analysis_dir" .venv/bin/python -m adwatch init
ADWATCH_DATA_DIR="$analysis_dir" .venv/bin/python -m adwatch collect --mode mock --date 2026-07-22
ADWATCH_DATA_DIR="$analysis_dir" .venv/bin/python -m adwatch seed-business-data --date 2026-07-22
ADWATCH_DATA_DIR="$analysis_dir" .venv/bin/python -m adwatch analyze --date 2026-07-22
```

Expected: eight metrics receive profit results; low-ROAS mock rows create recommendations without bypassing approval rules.

- [ ] **Step 6: Run full verification and commit**

Run: `.venv/bin/python -m pytest -q`

Run: `.venv/bin/python -m ruff check .`

Expected: all tests pass and lint is clean.

```bash
git add src/adwatch tests
git commit -m "feat: integrate analytics and strategy pipeline"
```

