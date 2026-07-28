# README Project Overview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the operations-heavy README with a GitHub-facing project feature overview backed by the current code and real activation status.

**Architecture:** Present product capabilities first, then the end-to-end data flow, safety model, concise technical architecture, quick start, and honest activation status. Keep detailed rollout procedures in existing design and plan documents instead of duplicating them in README.

**Tech Stack:** Markdown, Python CLI, SQLite, Ziniao CLI, pytest, Ruff.

---

### Task 1: Rewrite README as a product feature overview

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the current long-form operations structure**

Write these sections in order:

```markdown
# Adwatch
## 项目简介
## 解决的问题
## 核心功能
## 端到端工作流
## 广告调优与安全执行
## 经营与利润口径
## 系统架构
## 快速开始
## 当前状态
## 开发验证
```

The core feature list must cover real dual-platform collection, order and SKU
costs, SKU-scoped fulfillment, bookkeeping, partner profit sharing, three ROAS
views, recommendations, reconciliation, reports, dashboard, backups, and safe
Shadow/Live execution.

- [ ] **Step 2: State safety and activation status precisely**

Include these facts:

```markdown
- 真实广告写入默认关闭。
- 飞书批准只更新审批状态，不会自动执行广告修改。
- Live 还需要独立执行命令、有效审批、页面未漂移、熔断关闭、
  已激活选择器和精确白名单。
- Shopee 已完成真实采集及首日 100% 对账。
- TikTok 已登录，但当前没有有数据广告计划。
- 飞书公网回调暂缓现场启用；官方 API OAuth 可选。
```

- [ ] **Step 3: Keep only executable quick-start commands**

Use commands present in `src/adwatch/cli.py`:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
.venv/bin/adwatch init
.venv/bin/adwatch doctor
.venv/bin/adwatch collect --mode ziniao --date YYYY-MM-DD
.venv/bin/adwatch business sync-orders
.venv/bin/adwatch analyze --date YYYY-MM-DD
.venv/bin/adwatch dashboard --host 127.0.0.1 --port 8765
```

- [ ] **Step 4: Verify README against source**

Run:

```bash
rg -n "add_parser\\(\"(init|doctor|collect|analyze|dashboard)\"|sync-orders" \
  src/adwatch/cli.py
rg -n "FEISHU_|ZINIAO_|ADWATCH_" .env.example src/adwatch/config.py
git diff --check
```

Expected: every documented command and environment family exists; no
whitespace errors.

- [ ] **Step 5: Run regression verification**

```bash
PYTHONPATH=src /Users/yl/Documents/跨境电商/广告盯盘自动化/.venv/bin/python \
  -m pytest -q
PYTHONPATH=src /Users/yl/Documents/跨境电商/广告盯盘自动化/.venv/bin/python \
  -m ruff check src tests
```

Expected: 204 tests pass and Ruff reports no errors.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/superpowers/plans/2026-07-28-readme-project-overview.md
git commit -m "docs: rewrite README as project overview"
```

### Task 2: Integrate and publish

**Files:**
- No additional file changes.

- [ ] **Step 1: Fast-forward merge into main**

```bash
git merge --ff-only codex/readme-overview
```

- [ ] **Step 2: Re-run verification on main**

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python -m ruff check src tests
git diff --check
```

- [ ] **Step 3: Push the requested branch**

```bash
git push origin main
```

Expected: `origin/main` advances to the verified local `main` commit without
including unrelated untracked user files.
