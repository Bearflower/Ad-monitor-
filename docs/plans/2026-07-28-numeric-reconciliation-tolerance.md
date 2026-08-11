# Numeric Reconciliation Tolerance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compare displayed advertising metrics with stored metrics using field-aware numeric tolerance while preserving raw evidence.

**Architecture:** Keep comparison policy inside the reconciliation service so CLI and future callers share one rule. Categories select money, ratio, count, or normalized-text comparison; persisted expected/actual JSON remains unchanged.

**Tech Stack:** Python 3.11+, `Decimal`, SQLite, pytest, Ruff.

---

## File map

- Modify `src/adwatch/reconciliation/service.py`: field-aware comparison policy.
- Modify `tests/reconciliation/test_service.py`: boundary, rounding, count, and invalid-value coverage.
- Modify `tests/test_cli.py`: CSV integration coverage using real Shopee-shaped values.
- Create runtime evidence `var/reconciliation/shopee-2026-07-27.csv` in the main workspace after merge; this is operational data and is not committed.

### Task 1: Field-aware comparison policy

**Files:**
- Modify: `src/adwatch/reconciliation/service.py`
- Test: `tests/reconciliation/test_service.py`

- [ ] **Step 1: Write failing service tests**

Add tests that call `record_day` with:

```python
expected = {"spend": "400.00", "roas": "2.67", "orders": "5"}
actual = {"spend": "400", "roas": "2.6650", "orders": "5"}
categories = {"spend": "money", "roas": "ratio", "orders": "count"}
```

Assert accuracy is `Decimal("1.0000")`, raw values remain unchanged in
`reconciliation_days`, a money difference greater than `0.01` fails, a count
difference fails, and an invalid numeric value fails.

- [ ] **Step 2: Verify RED**

Run:

```bash
PYTHONPATH=src /Users/yl/Documents/跨境电商/广告盯盘自动化/.venv/bin/python \
  -m pytest tests/reconciliation/test_service.py -q
```

Expected: the ROAS rounding test fails because `2.67 != 2.6650`.

- [ ] **Step 3: Implement the minimal comparison function**

Add to `service.py`:

```python
NUMERIC_TOLERANCE = Decimal("0.01")


def _matches(expected: object, actual: object, category: str) -> bool:
    if actual is None:
        return False
    if category in {"money", "ratio"}:
        try:
            return abs(Decimal(str(expected)) - Decimal(str(actual))) <= (
                NUMERIC_TOLERANCE
            )
        except InvalidOperation:
            return False
    if category == "count":
        try:
            left = Decimal(str(expected))
            right = Decimal(str(actual))
        except InvalidOperation:
            return False
        return (
            left == left.to_integral_value()
            and right == right.to_integral_value()
            and left == right
        )
    return str(expected).strip() == str(actual).strip()
```

Import `InvalidOperation`, and replace direct `actual.get(field) != value`
comparison in `record_day` with:

```python
if not _matches(value, actual.get(field), categories.get(field, "unknown"))
```

- [ ] **Step 4: Verify GREEN**

Run the service tests again. Expected: all reconciliation service tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/adwatch/reconciliation/service.py \
  tests/reconciliation/test_service.py
git commit -m "feat: add numeric reconciliation tolerance"
```

### Task 2: CLI evidence import and field verification

**Files:**
- Modify: `tests/test_cli.py`
- Operational create after merge: `var/reconciliation/shopee-2026-07-27.csv`

- [ ] **Step 1: Write the failing CLI integration test**

Change the reconciliation CSV test to include:

```csv
field,expected,actual,category
spend,400.00,400.00,money
gmv,1066.00,1066.00,money
orders,5,5,count
roas,2.67,2.6650,ratio
```

Assert import prints `accuracy=1.0000` and the report also returns
`accuracy=1.0000`.

- [ ] **Step 2: Verify RED or coverage**

Run:

```bash
PYTHONPATH=src /Users/yl/Documents/跨境电商/广告盯盘自动化/.venv/bin/python \
  -m pytest tests/test_cli.py::test_reconciliation_csv_import_and_report -q
```

Expected after Task 1: PASS, confirming the CLI uses the shared policy. If it
fails, correct only CSV parsing/integration behavior and do not duplicate
comparison logic in the CLI.

- [ ] **Step 3: Run complete verification**

```bash
PYTHONPATH=src /Users/yl/Documents/跨境电商/广告盯盘自动化/.venv/bin/python \
  -m pytest -q
PYTHONPATH=src /Users/yl/Documents/跨境电商/广告盯盘自动化/.venv/bin/python \
  -m ruff check src tests
git diff --check
```

Expected: all tests and Ruff pass with no whitespace errors.

- [ ] **Step 4: Commit CLI coverage**

```bash
git add tests/test_cli.py
git commit -m "test: cover rounded metrics reconciliation"
```

- [ ] **Step 5: Merge and record the first real day**

Fast-forward the verified branch into `main`. In the main workspace create
`var/reconciliation/shopee-2026-07-27.csv` with the four verified page/database
values above, then run:

```bash
.venv/bin/adwatch reconcile import \
  --platform shopee --store 虾皮泰国 --date 2026-07-27 \
  --file var/reconciliation/shopee-2026-07-27.csv
.venv/bin/adwatch reconcile report \
  --platform shopee --store 虾皮泰国 \
  --from 2026-07-27 --to 2026-07-27
```

Expected: both commands report `accuracy=1.0000`.

- [ ] **Step 6: Refresh launch readiness**

Run:

```bash
.venv/bin/adwatch launch-checklist --format markdown
```

Expected: the three-day gate remains pending until two additional consecutive
real days exist; TikTok validation remains pending while its campaign list is
empty.
