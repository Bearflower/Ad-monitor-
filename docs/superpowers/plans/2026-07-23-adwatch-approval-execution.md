# Adwatch Approval and Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add auditable human approval, drift-safe idempotent execution, and a tested Ziniao WebDriver HTTP control client without pretending platform page operations are available before account setup.

**Architecture:** Recommendations become immutable approval requests. The executor accepts only approved, unexpired requests while the circuit is closed, re-reads current state, rejects drift, and writes an audit record for every attempt. A transport-injected Ziniao client implements official local HTTP actions; platform-specific report extraction and ad edits remain disabled until real accounts can be inspected and validated.

**Tech Stack:** Python 3.12 standard library, SQLite, urllib, pytest, Ruff.

---

### Task 1: Approval and audit schema

- Add migration v3 tables `approvals` and `execution_audits`.
- Approval rows reference recommendations, include status, requested/expiry/decision timestamps, approver, decision token hash, and unique recommendation ID.
- Audit rows include approval ID, action, before/after JSON, screenshot paths, status, error, and idempotency key.
- Test migration and commit `feat: add approval audit schema`.

### Task 2: Approval service

- Implement creation only for recommendations with `requires_approval=1`.
- Generate a random raw decision token, store only its SHA-256 hash, default expiry 24 hours.
- Implement approve/reject with constant-time token comparison and terminal-state protection.
- Test invalid token, expiry, duplicate decisions, and successful approval.
- Commit `feat: add secure approval workflow`.

### Task 3: Safe executor

- Define an `ExecutionBackend` protocol with `read_current`, `execute`, and `capture`.
- Reject open circuit, non-approved/expired approval, state drift, blocked action, and reused idempotency key.
- Permanently block delete, account/store/security changes, new large campaigns, and budget increases above 50%.
- Record every allowed or rejected attempt; capture before and after evidence for allowed operations.
- Test successful dry-run, drift rejection, circuit rejection, and duplicate execution.
- Commit `feat: add drift-safe approved executor`.

### Task 4: Ziniao WebDriver client and diagnostics

- Implement UTF-8 HTTP POST actions `getBrowserList`, `startBrowser`, `stopBrowser`, and `exit` with UUID request IDs and 120-second timeout.
- Include full company/username/password credentials for protected actions and prefer the returned `err` field in exceptions.
- Add a fake-transport contract test for request shapes and error decoding.
- Extend `doctor` to test endpoint reachability only when all credentials exist; never print secrets.
- Do not implement TikTok/Shopee selectors before real store inspection.
- Run the full suite and commit `feat: add Ziniao WebDriver control client`.

