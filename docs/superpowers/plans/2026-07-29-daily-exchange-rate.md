# Daily Exchange Rate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在每日利润分析前同步报表日汇率，并在网络失败时安全沿用 7 天内的最近本地汇率。

**Architecture:** 汇率集成模块新增单一 `ensure_exchange_rate` 服务，负责远程同步和有界本地回退。CLI 每日流程只调用该服务并记录状态；服务失败不阻断采集和飞书，分析继续通过现有缺数据门禁保护利润。

**Tech Stack:** Python、SQLite、ECB XML、pytest。

---

### Task 1：汇率同步与有界回退

**Files:**
- Modify: `src/adwatch/integrations/exchange_rates.py`
- Create: `tests/integrations/test_exchange_rate_ensure.py`

- [ ] 写失败测试：远程失败时复制 7 天内最近汇率。
- [ ] 写失败测试：8 天前汇率必须拒绝。
- [ ] 实现 `ExchangeRateResolution` 和 `ensure_exchange_rate`。
- [ ] 运行 `tests/integrations/test_exchange_rate_ensure.py`。
- [ ] 提交 `feat: add bounded exchange rate fallback`。

### Task 2：接入每日流程

**Files:**
- Modify: `src/adwatch/cli.py`
- Modify: `tests/test_daily_run_cli.py`

- [ ] 写失败测试：汇率服务在 `AnalysisService.run` 之前调用。
- [ ] 写失败测试：汇率服务失败不阻断报告发送。
- [ ] 在 `run daily` 中接入 THB/CNY 汇率服务并输出状态。
- [ ] 运行每日流程和 CLI 测试。
- [ ] 提交 `fix: sync exchange rates before daily analysis`。

### Task 3：验证、合并和重发

- [ ] 运行全量 pytest 和 Ruff。
- [ ] 合并到 main。
- [ ] 在正式目录重跑 `2026-07-28` 真实每日任务。
- [ ] 验证利润结果、告警和飞书 `delivery=sent`。
- [ ] 备份并更新上线待办。
