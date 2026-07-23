# Adwatch Data Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a runnable Python CLI that initializes SQLite, collects deterministic TikTok and Shopee mock data, validates and upserts it idempotently, and emits a machine-readable data-quality report.

**Architecture:** A small `src`-layout Python package separates domain records, collector contracts, SQLite persistence, pipeline validation, and CLI orchestration. Mock and future Ziniao collectors implement the same protocol. The daily metrics table uses a stable logical key so replaying a collection run updates rather than duplicates records.

**Tech Stack:** Python 3.11+, standard-library `argparse`, `sqlite3`, `dataclasses`, `decimal`, `json`, `pytest`, `ruff`.

---

## File map

- `pyproject.toml`: package metadata, CLI entry point, pytest and Ruff configuration.
- `.env.example`: documented secret/configuration names without values.
- `.gitignore`: ignores runtime data, secrets, caches, and build products.
- `src/adwatch/__init__.py`: package version.
- `src/adwatch/__main__.py`: supports `python -m adwatch`.
- `src/adwatch/cli.py`: parses commands and maps exit codes.
- `src/adwatch/config.py`: resolves runtime paths and modes.
- `src/adwatch/domain.py`: immutable normalized metric and validation records.
- `src/adwatch/collectors/base.py`: collector protocol and collection result.
- `src/adwatch/collectors/mock.py`: deterministic dual-platform fixtures.
- `src/adwatch/collectors/ziniao.py`: explicit configuration gate for the future real adapter.
- `src/adwatch/storage/db.py`: connection and transaction lifecycle.
- `src/adwatch/storage/migrations.py`: versioned SQLite DDL.
- `src/adwatch/storage/metrics.py`: idempotent metric writes and reads.
- `src/adwatch/storage/runs.py`: collection-run and quality-result persistence.
- `src/adwatch/pipeline/validation.py`: record-level rules and quarantine decisions.
- `src/adwatch/pipeline/runner.py`: collection → validation → persistence orchestration.
- `src/adwatch/reporting/quality.py`: JSON and console quality reports.
- `tests/`: unit, integration, and CLI tests mirroring package boundaries.

### Task 1: Package and CLI bootstrap

**Files:**
- Create: `pyproject.toml`
- Create: `src/adwatch/__init__.py`
- Create: `src/adwatch/__main__.py`
- Create: `src/adwatch/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing CLI smoke test**

```python
from adwatch.cli import main


def test_cli_help_exits_successfully(capsys):
    assert main(["--help"]) == 0
    assert "collect" in capsys.readouterr().out
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `python -m pytest tests/test_cli.py -q`

Expected: FAIL because `adwatch` does not exist.

- [ ] **Step 3: Add package metadata and the minimal command parser**

`pyproject.toml` must declare a `src` package, Python `>=3.11`, the `adwatch = "adwatch.cli:entrypoint"` script, and a `dev` extra containing `pytest>=8,<9` and `ruff>=0.6,<1`.

`src/adwatch/cli.py`:

```python
import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="adwatch")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("init")
    collect = sub.add_parser("collect")
    collect.add_argument("--mode", choices=("mock", "ziniao"), default="mock")
    sub.add_parser("doctor")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    if args.command is None:
        parser.print_help()
    return 0


def entrypoint() -> None:
    raise SystemExit(main())
```

`src/adwatch/__main__.py` calls `entrypoint()`, and `src/adwatch/__init__.py` defines `__version__ = "0.1.0"`.

- [ ] **Step 4: Install editable development dependencies and run the test**

Run: `python -m pip install -e '.[dev]'`

Run: `python -m pytest tests/test_cli.py -q`

Expected: 1 passed.

- [ ] **Step 5: Commit the bootstrap**

```bash
git add pyproject.toml src/adwatch tests/test_cli.py
git commit -m "feat: bootstrap adwatch CLI"
```

### Task 2: Runtime configuration and secret hygiene

**Files:**
- Modify: `.gitignore`
- Create: `.env.example`
- Create: `src/adwatch/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing path-resolution tests**

```python
from pathlib import Path

from adwatch.config import Settings


def test_settings_use_explicit_data_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("ADWATCH_DATA_DIR", str(tmp_path))
    settings = Settings.from_env()
    assert settings.database_path == tmp_path / "adwatch.sqlite3"
    assert settings.report_dir == tmp_path / "reports"


def test_ziniao_readiness_requires_all_values(monkeypatch):
    monkeypatch.setenv("ZINIAO_COMPANY", "demo")
    assert Settings.from_env().ziniao_ready is False
```

- [ ] **Step 2: Run tests and verify missing module failure**

Run: `python -m pytest tests/test_config.py -q`

Expected: FAIL because `adwatch.config` does not exist.

- [ ] **Step 3: Implement immutable settings**

Create a frozen `Settings` dataclass with `data_dir`, `database_path`, `report_dir`, `ziniao_company`, `ziniao_username`, `ziniao_password`, `ziniao_endpoint`, and `feishu_webhook`. `from_env()` uses `ADWATCH_DATA_DIR` or defaults to `Path.cwd() / "var"`. `ziniao_ready` is true only when company, username, password, and endpoint are all non-empty.

`.env.example` lists:

```dotenv
ADWATCH_DATA_DIR=./var
ZINIAO_COMPANY=
ZINIAO_USERNAME=
ZINIAO_PASSWORD=
ZINIAO_ENDPOINT=http://127.0.0.1:1886
FEISHU_WEBHOOK=
```

Extend `.gitignore` with `.env`, `var/`, `*.sqlite3`, `.pytest_cache/`, `.ruff_cache/`, `__pycache__/`, and `*.egg-info/`.

- [ ] **Step 4: Run configuration tests**

Run: `python -m pytest tests/test_config.py -q`

Expected: 2 passed.

- [ ] **Step 5: Commit configuration**

```bash
git add .gitignore .env.example src/adwatch/config.py tests/test_config.py
git commit -m "feat: add runtime configuration"
```

### Task 3: Normalized metric domain model

**Files:**
- Create: `src/adwatch/domain.py`
- Test: `tests/test_domain.py`

- [ ] **Step 1: Write failing normalization tests**

```python
from datetime import date
from decimal import Decimal

from adwatch.domain import DailyAdMetric, Platform


def test_metric_calculates_roas_and_cpa():
    metric = DailyAdMetric(
        platform=Platform.TIKTOK,
        store="MY Store",
        account_id="acct-1",
        campaign_id="camp-1",
        sku_id="SKU-1",
        data_date=date(2026, 7, 22),
        currency="MYR",
        spend=Decimal("100.00"),
        attributed_gmv=Decimal("350.00"),
        orders=7,
        source="mock",
    )
    assert metric.roas == Decimal("3.5000")
    assert metric.cpa == Decimal("14.2857")
    assert metric.logical_key == (
        "tiktok", "MY Store", "acct-1", "camp-1", "SKU-1", "2026-07-22"
    )
```

- [ ] **Step 2: Run test and verify failure**

Run: `python -m pytest tests/test_domain.py -q`

Expected: FAIL because `adwatch.domain` does not exist.

- [ ] **Step 3: Implement the immutable record**

Create `Platform(str, Enum)` with `TIKTOK` and `SHOPEE`. Create a frozen `DailyAdMetric` dataclass with the fields used in the test. `roas` and `cpa` return `None` for zero denominators and otherwise quantize to `Decimal("0.0001")`. `logical_key` uses the exact tuple asserted above.

Also create frozen `ValidationIssue` with `code`, `field`, `message`, and `severity`, plus `ValidatedMetric` with `metric` and `issues`.

- [ ] **Step 4: Run domain tests**

Run: `python -m pytest tests/test_domain.py -q`

Expected: 1 passed.

- [ ] **Step 5: Commit the domain model**

```bash
git add src/adwatch/domain.py tests/test_domain.py
git commit -m "feat: define normalized ad metrics"
```

### Task 4: SQLite migrations and connection lifecycle

**Files:**
- Create: `src/adwatch/storage/__init__.py`
- Create: `src/adwatch/storage/db.py`
- Create: `src/adwatch/storage/migrations.py`
- Test: `tests/storage/test_migrations.py`

- [ ] **Step 1: Write a failing migration test**

```python
from adwatch.storage.db import Database


def test_database_migrates_required_tables(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    db.migrate()
    names = {
        row[0]
        for row in db.connect().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {
        "schema_migrations",
        "daily_ad_metrics",
        "collection_runs",
        "quality_checks",
        "quarantined_records",
    } <= names
```

- [ ] **Step 2: Run the test and verify failure**

Run: `python -m pytest tests/storage/test_migrations.py -q`

Expected: FAIL because the storage package does not exist.

- [ ] **Step 3: Implement database setup and version 1 migration**

`Database.connect()` creates parent directories, returns a connection with `row_factory=sqlite3.Row`, enables foreign keys, and sets `PRAGMA journal_mode=WAL`. `Database.transaction()` commits on success and rolls back on exception.

Migration version 1 creates:

- `daily_ad_metrics` with normalized text/number columns and a unique constraint on platform, store, account, campaign, SKU, and date.
- `collection_runs` with UUID, mode, platform, start/end timestamps, status, counts, and error code/message.
- `quality_checks` linked to a run.
- `quarantined_records` linked to a run with raw JSON and issues JSON.
- `schema_migrations` with integer version and applied timestamp.

All timestamps use UTC ISO-8601 text. All money is stored as decimal text, never SQLite floating point.

- [ ] **Step 4: Run the migration test**

Run: `python -m pytest tests/storage/test_migrations.py -q`

Expected: 1 passed.

- [ ] **Step 5: Commit migrations**

```bash
git add src/adwatch/storage tests/storage/test_migrations.py
git commit -m "feat: add SQLite migrations"
```

### Task 5: Collector contract and deterministic mock collectors

**Files:**
- Create: `src/adwatch/collectors/__init__.py`
- Create: `src/adwatch/collectors/base.py`
- Create: `src/adwatch/collectors/mock.py`
- Create: `src/adwatch/collectors/ziniao.py`
- Test: `tests/collectors/test_mock.py`
- Test: `tests/collectors/test_ziniao.py`

- [ ] **Step 1: Write failing collector contract tests**

```python
from datetime import date

import pytest

from adwatch.collectors.mock import MockCollector
from adwatch.collectors.ziniao import ZiniaoCollector, ZiniaoNotConfigured
from adwatch.config import Settings
from adwatch.domain import Platform


def test_mock_collectors_are_deterministic():
    first = MockCollector(Platform.TIKTOK).collect(date(2026, 7, 22))
    second = MockCollector(Platform.TIKTOK).collect(date(2026, 7, 22))
    assert first == second
    assert first
    assert all(item.platform is Platform.TIKTOK for item in first)


def test_ziniao_collector_fails_explicitly_when_unconfigured(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with pytest.raises(ZiniaoNotConfigured):
        ZiniaoCollector(settings, Platform.SHOPEE).collect(date(2026, 7, 22))
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/collectors -q`

Expected: FAIL because collector modules do not exist.

- [ ] **Step 3: Implement collector protocol and adapters**

Define:

```python
class Collector(Protocol):
    platform: Platform
    source: str

    def collect(self, data_date: date) -> list[DailyAdMetric]: ...
```

`MockCollector` generates at least four fixed campaigns per platform using a seeded `random.Random(f"{platform.value}:{data_date.isoformat()}")`. Values must be valid Decimals and include one low-ROAS row for later anomaly tests.

`ZiniaoCollector` checks `settings.ziniao_ready`. When false, raise `ZiniaoNotConfigured` with a message naming the missing environment variables. When true, raise `NotImplementedError("Ziniao transport is delivered in the real-collector plan")`; this makes the boundary explicit without silently returning mock data.

- [ ] **Step 4: Run collector tests**

Run: `python -m pytest tests/collectors -q`

Expected: 2 passed.

- [ ] **Step 5: Commit collectors**

```bash
git add src/adwatch/collectors tests/collectors
git commit -m "feat: add collector contracts and mock data"
```

### Task 6: Validation and quarantine rules

**Files:**
- Create: `src/adwatch/pipeline/__init__.py`
- Create: `src/adwatch/pipeline/validation.py`
- Test: `tests/pipeline/test_validation.py`

- [ ] **Step 1: Write failing validation tests**

```python
from dataclasses import replace
from datetime import date
from decimal import Decimal

from adwatch.domain import DailyAdMetric, Platform
from adwatch.pipeline.validation import validate_metric


BASE = DailyAdMetric(
    platform=Platform.SHOPEE,
    store="TH Store",
    account_id="acct",
    campaign_id="camp",
    sku_id="SKU",
    data_date=date(2026, 7, 22),
    currency="THB",
    spend=Decimal("10"),
    attributed_gmv=Decimal("20"),
    orders=1,
    source="mock",
)


def test_negative_spend_is_quarantined():
    result = validate_metric(replace(BASE, spend=Decimal("-1")))
    assert result.is_valid is False
    assert {issue.code for issue in result.issues} == {"negative_spend"}


def test_unknown_currency_is_quarantined():
    result = validate_metric(replace(BASE, currency="XYZ"))
    assert result.is_valid is False
    assert {issue.code for issue in result.issues} == {"unknown_currency"}
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/pipeline/test_validation.py -q`

Expected: FAIL because validation does not exist.

- [ ] **Step 3: Implement deterministic validation**

Add `ValidatedMetric.is_valid`, true only when no issue has severity `"error"`. Reject blank store/account/campaign/SKU identifiers, negative spend/GMV/orders, dates in the future, and currencies outside `CNY, USD, MYR, THB, PHP, IDR, VND, SGD, BRL`. Return all detected issues in stable field order.

- [ ] **Step 4: Run validation tests**

Run: `python -m pytest tests/pipeline/test_validation.py -q`

Expected: 2 passed.

- [ ] **Step 5: Commit validation**

```bash
git add src/adwatch/pipeline tests/pipeline/test_validation.py
git commit -m "feat: validate normalized metrics"
```

### Task 7: Idempotent repositories and pipeline runner

**Files:**
- Create: `src/adwatch/storage/metrics.py`
- Create: `src/adwatch/storage/runs.py`
- Create: `src/adwatch/pipeline/runner.py`
- Test: `tests/pipeline/test_runner.py`

- [ ] **Step 1: Write a failing replay test**

```python
from datetime import date

from adwatch.collectors.mock import MockCollector
from adwatch.domain import Platform
from adwatch.pipeline.runner import PipelineRunner
from adwatch.storage.db import Database


def test_replaying_same_day_is_idempotent(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    db.migrate()
    runner = PipelineRunner(db)
    collector = MockCollector(Platform.TIKTOK)

    first = runner.run(collector, date(2026, 7, 22))
    second = runner.run(collector, date(2026, 7, 22))

    assert first.accepted > 0
    assert second.accepted == first.accepted
    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM daily_ad_metrics").fetchone()[0]
    assert count == first.accepted
```

- [ ] **Step 2: Run test and verify failure**

Run: `python -m pytest tests/pipeline/test_runner.py -q`

Expected: FAIL because repositories and runner do not exist.

- [ ] **Step 3: Implement transactional upsert and run tracking**

`MetricRepository.upsert_many()` uses `INSERT ... ON CONFLICT (...) DO UPDATE` and returns the number accepted. `RunRepository` creates a UUID run in `"running"` status, records quality counts, persists quarantined raw data as sorted JSON, and closes the run as `"succeeded"` or `"failed"`.

`PipelineRunner.run()`:

1. Opens a run.
2. Calls the collector.
3. Validates every record.
4. In one transaction, upserts valid records and stores invalid records.
5. Closes the run and returns `PipelineSummary(run_id, platform, source, received, accepted, quarantined)`.
6. On exceptions, closes the run as failed and re-raises.

- [ ] **Step 4: Run pipeline tests**

Run: `python -m pytest tests/pipeline/test_runner.py -q`

Expected: 1 passed.

- [ ] **Step 5: Commit pipeline persistence**

```bash
git add src/adwatch/storage src/adwatch/pipeline/runner.py tests/pipeline/test_runner.py
git commit -m "feat: persist collection runs idempotently"
```

### Task 8: Quality reporting and end-to-end CLI

**Files:**
- Create: `src/adwatch/reporting/__init__.py`
- Create: `src/adwatch/reporting/quality.py`
- Modify: `src/adwatch/cli.py`
- Test: `tests/test_cli_end_to_end.py`

- [ ] **Step 1: Write the failing CLI end-to-end test**

```python
import json

from adwatch.cli import main


def test_mock_collection_writes_database_and_quality_report(tmp_path, monkeypatch):
    monkeypatch.setenv("ADWATCH_DATA_DIR", str(tmp_path))
    assert main(["init"]) == 0
    assert main(["collect", "--mode", "mock", "--date", "2026-07-22"]) == 0

    report = json.loads((tmp_path / "reports" / "quality-2026-07-22.json").read_text())
    assert {item["platform"] for item in report["runs"]} == {"tiktok", "shopee"}
    assert report["totals"]["accepted"] >= 8
    assert report["totals"]["quarantined"] == 0
```

- [ ] **Step 2: Run the test and verify failure**

Run: `python -m pytest tests/test_cli_end_to_end.py -q`

Expected: FAIL because CLI commands do not run the pipeline or write reports.

- [ ] **Step 3: Implement CLI orchestration and JSON report**

Add `--date YYYY-MM-DD` to `collect`. `init` creates runtime directories and migrates the database. `collect --mode mock` migrates, runs both platform collectors, writes `quality-<date>.json` atomically, and prints a concise table. `collect --mode ziniao` exits with code 2 and a clear configuration message if Ziniao is not ready.

The JSON report contains:

```json
{
  "data_date": "2026-07-22",
  "source": "mock",
  "simulated": true,
  "runs": [],
  "totals": {"received": 0, "accepted": 0, "quarantined": 0}
}
```

Write to a sibling temporary file and use `Path.replace()` to prevent partial reports.

- [ ] **Step 4: Run all tests and lint**

Run: `python -m pytest -q`

Expected: all tests pass.

Run: `python -m ruff check .`

Expected: no lint errors.

- [ ] **Step 5: Manually verify the user-facing command**

Run: `ADWATCH_DATA_DIR=/tmp/adwatch-plan-check python -m adwatch init`

Run: `ADWATCH_DATA_DIR=/tmp/adwatch-plan-check python -m adwatch collect --mode mock --date 2026-07-22`

Expected: output lists both platforms, accepted counts, zero quarantined records, database path, and report path.

- [ ] **Step 6: Commit the working data foundation**

```bash
git add src/adwatch tests
git commit -m "feat: complete mock collection pipeline"
```

### Task 9: Phase documentation and verification

**Files:**
- Create: `README.md`
- Create: `docs/operations/data-foundation.md`

- [ ] **Step 1: Write operator documentation**

`README.md` must contain prerequisites, editable install, `.env.example` usage, `init`, `doctor`, and mock collection commands. It must state that mock reports are visibly marked simulated and that real Ziniao collection is not silently substituted.

`docs/operations/data-foundation.md` must explain runtime files, SQLite backup by copying the database while the application is stopped, rerun/idempotency behavior, quarantine inspection, and the exact environment variables required for the real adapter.

- [ ] **Step 2: Run documentation command examples**

Run every setup and mock command shown in `README.md` against a fresh temporary data directory.

Expected: commands exit 0 and produce the documented files.

- [ ] **Step 3: Run final verification**

Run: `python -m pytest -q`

Expected: all tests pass.

Run: `python -m ruff check .`

Expected: no lint errors.

Run: `git status --short`

Expected: only the two new documentation files are uncommitted.

- [ ] **Step 4: Commit documentation**

```bash
git add README.md docs/operations/data-foundation.md
git commit -m "docs: document data foundation operations"
```

## Follow-on plans

After this plan passes verification, create and execute these separate plans against the working data foundation:

1. `adwatch-analytics-strategy`: profit model, exchange rates, inventory, 7/14/30-day trends, anomaly rules, learning-period protection, recommendations, and circuit breakers.
2. `adwatch-dashboard-reporting`: local dashboard, daily/weekly reports, Feishu delivery, failure fallback, and macOS scheduling.
3. `adwatch-approval-execution`: Feishu approvals, callback verification, immutable audit trail, screenshots, Ziniao real collection, approved write operations, drift checks, and recovery.

