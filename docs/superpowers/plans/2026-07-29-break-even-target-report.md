# Break-even Target Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add conservative business break-even ROAS, GMV, order target, gap, and confidence to the daily Markdown and Feishu report.

**Architecture:** Put pure decimal calculations in a focused reporting module. Enrich `PlatformSummary` in the read model with the calculated target and matched-cost order evidence, then render a dedicated Chinese report section. No schema migration or advertising write behavior is added.

**Tech Stack:** Python 3.11, dataclasses, Decimal, SQLite, pytest, existing Feishu Markdown/card delivery.

---

### Task 1: Pure break-even calculator

**Files:**
- Create: `src/adwatch/reporting/break_even.py`
- Create: `tests/reporting/test_break_even.py`

- [ ] **Step 1: Write failing sample and invalid-input tests**

Test `calculate_break_even()` with spend `210.10`, GMV `179`, orders `2`, sales CNY `36.06`, fee `8.55`, variable cost `9.81`, and matched cost orders `1`. Assert ROAS `2.04`, GMV `428.03`, AOV `89.50`, target orders `5`, gaps `249.03/3`, and confidence `reconciliation_pending`. Also assert zero spend, GMV, orders, non-positive contribution, and missing inputs return an unavailable result.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest tests/reporting/test_break_even.py -q`  
Expected: FAIL because `adwatch.reporting.break_even` does not exist.

- [ ] **Step 3: Implement minimal typed calculator**

Create immutable `BreakEvenTarget` fields `break_even_roas`, `break_even_gmv`, `average_order_value`, `break_even_orders`, `gmv_gap`, `order_gap`, `confidence`, and `explanation`. Implement the confirmed contribution-margin formulas with `ROUND_CEILING` for order targets and two-decimal display quantization. Use confidence `verified` when matched orders cover attributed orders, `reconciliation_pending` when they do not, and `missing_data` when unavailable.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/reporting/test_break_even.py -q`  
Expected: all tests pass.

### Task 2: Read-model enrichment and cost-order evidence

**Files:**
- Modify: `src/adwatch/reporting/read_model.py`
- Modify: `tests/reporting/test_read_model.py`

- [ ] **Step 1: Write failing integration test**

Seed one Shopee daily metric, profit result, store alias, two platform-attributed orders, and one confirmed cost order. Assert `PlatformSummary.break_even_target` contains `2.04`, `428.03`, `5`, and confidence `reconciliation_pending`, with attributed orders `2` and matched cost orders `1`.

- [ ] **Step 2: Run test and verify RED**

Run: `.venv/bin/python -m pytest tests/reporting/test_read_model.py -q`  
Expected: FAIL because `PlatformSummary` has no break-even target.

- [ ] **Step 3: Extend the daily SQL and summary**

Add platform/day distinct order counts from `platform_order_lines`, matching confirmed rows from `order_cost_snapshots` through platform/store/order ID and `store_aliases`. Pass the existing profit breakdown plus order evidence to `calculate_break_even()`. Do not equate `Items Sold` with orders and do not modify recommendation tables.

- [ ] **Step 4: Run read-model tests**

Run: `.venv/bin/python -m pytest tests/reporting/test_read_model.py -q`  
Expected: all tests pass.

### Task 3: Markdown and Feishu-card presentation

**Files:**
- Modify: `src/adwatch/reporting/markdown.py`
- Modify: `tests/reporting/test_markdown.py`

- [ ] **Step 1: Write failing presentation tests**

Assert a new `二、保本目标` section includes current ROAS `0.85`, business break-even ROAS `2.04`, break-even GMV `428.03`, AOV `89.50`, target `约 5 单`, gap `249.03 / 约 3 单`, confidence `待对账`, and the `2/1` order explanation. Assert unavailable targets render `暂不可计算` without fabricated numbers and later section numbers are shifted.

- [ ] **Step 2: Run test and verify RED**

Run: `.venv/bin/python -m pytest tests/reporting/test_markdown.py -q`  
Expected: FAIL because the section is absent.

- [ ] **Step 3: Render the new section**

Add Chinese confidence labels and `_break_even_lines()`. Place the section after core profit results, shift platform/risk/action/confidence headings to three through six, and retain the existing presentation object so the Feishu card receives the same Markdown automatically.

- [ ] **Step 4: Run presentation tests**

Run: `.venv/bin/python -m pytest tests/reporting/test_markdown.py -q`  
Expected: all tests pass.

### Task 4: Safety, full verification, and live replay

**Files:**
- Modify if required by failing safety test: `src/adwatch/strategy/rules.py`
- Test: `tests/strategy/test_rules.py`
- Update: `docs/superpowers/specs/2026-07-29-break-even-target-report-design.md`

- [ ] **Step 1: Verify no automatic budget increase is derived from the report target**

Add or retain a test proving `reconciliation_pending` break-even evidence is presentation-only and no `increase_budget` recommendation is created from it.

- [ ] **Step 2: Run full verification**

Run: `.venv/bin/python -m pytest -q` and `.venv/bin/ruff check src tests` and `git diff --check`.  
Expected: zero failures and zero lint/diff errors.

- [ ] **Step 3: Merge, replay, and verify**

Merge the verified branch into `main`, back up `var/adwatch.sqlite3`, run `.venv/bin/adwatch run daily --mode ziniao --date 2026-07-28`, and verify `var/reports/daily-2026-07-28.md` contains the five break-even values and `待对账`. Confirm Feishu delivery reports `sent`.

- [ ] **Step 4: Commit and push**

Commit only source, tests, plan/spec files with `feat: add break-even targets to daily reports`, then push `main` to `origin`.
