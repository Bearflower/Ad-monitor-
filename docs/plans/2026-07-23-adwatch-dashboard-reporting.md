# Adwatch Dashboard and Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate daily/weekly Markdown reports, deliver them to Feishu with local fallback, run the whole daily pipeline with one command, and expose a read-only local dashboard.

**Architecture:** Reporting queries SQLite through a focused read model. Delivery uses a small transport protocol so tests never call the network. The dashboard is a dependency-free local HTTP server rendering escaped HTML and JSON from the same read model.

**Tech Stack:** Python 3.12 standard library, SQLite, urllib, http.server, pytest, Ruff.

---

### Task 1: Daily and weekly Markdown reports

**Files:**
- Create: `src/adwatch/reporting/read_model.py`
- Create: `src/adwatch/reporting/markdown.py`
- Test: `tests/reporting/test_markdown.py`

- [ ] Write a failing test that seeds the existing mock pipeline and asserts the daily report contains `【TikTok】`, `【Shopee】`, `【异常告警】`, `【TOP3/BOTTOM3】`, and `模拟数据`.
- [ ] Run `.venv/bin/python -m pytest tests/reporting/test_markdown.py -q`; expect failure because the renderer is absent.
- [ ] Implement `ReportReadModel.daily(date)` and `render_daily_markdown(snapshot, simulated=True)`. Use aggregated spend, GMV, orders, weighted ROAS, profit totals, alerts, and the three highest/lowest SKU ROAS rows.
- [ ] Implement weekly rendering from the last seven dates using the same structures.
- [ ] Run the focused test and commit with `feat: generate daily and weekly reports`.

### Task 2: Feishu delivery with local fallback

**Files:**
- Create: `src/adwatch/reporting/delivery.py`
- Test: `tests/reporting/test_delivery.py`

- [ ] Write a failing test with a transport that raises `OSError`; assert `deliver_report` returns `"fallback"` and atomically writes the Markdown file.
- [ ] Run the focused test; expect missing module failure.
- [ ] Define `WebhookTransport.send(url, payload)` using `urllib.request` with a 10-second timeout. Redact the webhook from errors. `deliver_report` sends a Feishu interactive-card payload when configured; after three bounded attempts it writes `reports/daily-<date>.md`.
- [ ] Run tests and commit with `feat: add Feishu report delivery fallback`.

### Task 3: Daily orchestration CLI

**Files:**
- Modify: `src/adwatch/cli.py`
- Test: `tests/test_daily_run_cli.py`

- [ ] Write a failing test invoking `run daily --mode mock --date 2026-07-22`; assert exit 0 and existence of database, quality JSON, and daily Markdown.
- [ ] Run the test; expect parser failure.
- [ ] Implement `run daily` in this order: migrate, collect both platforms, seed mock business data only in mock mode, analyze, render report, attempt delivery, print a one-line stage summary. A failed Feishu delivery must still exit 0 when fallback succeeds.
- [ ] Add `schedule --print-launchd` that prints a macOS plist command for daily 08:00 execution without installing it.
- [ ] Run full tests and commit with `feat: orchestrate daily advertising workflow`.

### Task 4: Read-only local dashboard

**Files:**
- Create: `src/adwatch/dashboard/__init__.py`
- Create: `src/adwatch/dashboard/app.py`
- Modify: `src/adwatch/cli.py`
- Test: `tests/dashboard/test_app.py`

- [ ] Write failing tests for `render_dashboard(database, date)` asserting HTML escaping, platform summary, campaign table, profit, alerts, recommendations, and simulated-data banner.
- [ ] Run tests; expect missing module failure.
- [ ] Implement semantic HTML with responsive inline CSS, accessible tables, visible focus styles, and no write controls. Add `/api/snapshot?date=YYYY-MM-DD` JSON and `/` HTML through `ThreadingHTTPServer`.
- [ ] Add `dashboard --host 127.0.0.1 --port 8765 --date YYYY-MM-DD`. Reject non-loopback hosts unless `--allow-remote` is explicitly supplied.
- [ ] Run `.venv/bin/python -m pytest -q`, `.venv/bin/python -m ruff check .`, manually start the server and fetch both routes, then commit with `feat: add local read-only dashboard`.

