# 飞书经营日报卡片 v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将飞书日报升级为全中文、带利润拆解、风险颜色、建议动作与原因的经营卡片。

**Architecture:** `ReportReadModel` 聚合平台利润分项，`markdown` 负责中文业务表达和风险判定，`delivery` 根据同一风险结果设置飞书 Header 颜色。策略规则和广告执行边界保持不变。

**Tech Stack:** Python 3.12、SQLite、飞书自定义机器人卡片、pytest。

---

### Task 1：利润拆解读取模型

**Files:**
- Modify: `src/adwatch/reporting/read_model.py`
- Test: `tests/reporting/test_read_model.py`

- [ ] **Step 1: 写失败测试**

插入 `daily_ad_metrics` 与 `profit_results`，断言 `PlatformSummary` 包含
`attributed_sales_cny`、`platform_fee_cny`、`ad_spend_cny`、
`sku_and_other_cost_cny`、`net_profit`。

- [ ] **Step 2: 运行测试并确认字段不存在**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/reporting/test_read_model.py -q
```

Expected: FAIL，提示 `PlatformSummary` 缺少利润拆解属性。

- [ ] **Step 3: 实现最小聚合**

扩展 SQL 聚合 `net_sales_cny`、`platform_commission_cny` 和净利润；广告费用
使用平台币花费乘对应日期汇率。计算：

```python
sku_and_other = sales - platform_fee - ad_spend - net_profit
```

任何利润行缺失时，整组利润拆解为 `None`。

- [ ] **Step 4: 验证**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/reporting/test_read_model.py -q
```

Expected: PASS。

### Task 2：中文日报、风险和建议

**Files:**
- Modify: `src/adwatch/reporting/markdown.py`
- Test: `tests/reporting/test_markdown.py`

- [ ] **Step 1: 写失败测试**

覆盖中文状态、利润拆解、负利润红色风险、无建议时的“暂不调整”和有建议时
的动作/原因；断言报告不含 `pending_data` 与 `TOP/BOTTOM`。

- [ ] **Step 2: 运行并确认旧格式失败**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/reporting/test_markdown.py -q
```

Expected: FAIL，旧报告仍包含英文状态与旧排名标题。

- [ ] **Step 3: 实现中文映射与 `DailyReportPresentation`**

新增不可变结果：

```python
@dataclass(frozen=True)
class DailyReportPresentation:
    markdown: str
    risk_label: str
    header_template: str
```

`present_daily_report()` 同时生成 Markdown 与风险颜色；
`render_daily_markdown()` 保留为兼容包装。

- [ ] **Step 4: 验证**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/reporting/test_markdown.py -q
```

Expected: PASS。

### Task 3：飞书 Header 风险颜色

**Files:**
- Modify: `src/adwatch/reporting/delivery.py`
- Modify: `src/adwatch/cli.py`
- Test: `tests/reporting/test_delivery.py`
- Test: `tests/test_daily_run_cli.py`

- [ ] **Step 1: 写失败测试**

调用 `deliver_report(..., header_template="red", risk_label="高风险")`，断言
发送 payload 的 `card.header.template == "red"` 且标题包含“高风险”。

- [ ] **Step 2: 运行并确认参数不存在**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/reporting/test_delivery.py -q
```

Expected: FAIL，`deliver_report` 不接受风险参数。

- [ ] **Step 3: 实现并接入日报流程**

`deliver_report` 新增具有绿色默认值的关键字参数。`run daily` 调用
`present_daily_report()`，把 Markdown、风险名称和颜色传给投递函数。

- [ ] **Step 4: 全量验证**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
```

Expected: 全部 PASS。

### Task 4：真实卡片验收

**Files:**
- No source changes

- [ ] **Step 1: 用 2026-07-27 真实数据重新生成日报**

Run:

```bash
.venv/bin/adwatch report daily --date 2026-07-27
```

- [ ] **Step 2: 发送飞书测试卡片**

使用当前 `.env` Webhook 和 `deliver_report` 发送生成后的 Markdown。

- [ ] **Step 3: 核验**

确认命令返回 `delivery=sent`，本地报告包含利润拆解、中文风险和建议原因，
广告状态与 Live 写入配置未改变。
