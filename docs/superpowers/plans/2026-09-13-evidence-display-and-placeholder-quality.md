# Evidence Display and Placeholder Quality Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让建议证据默认可读，并把 Shopee 页面中的占位活动名正确标记为数据缺失。

**Architecture:** 在采集边界统一识别占位名称，保留稳定 ID 和事实但降低质量；在 Web 渲染边界把不可变规则输入解析为中文摘要，并将原始审计值安全放入折叠区。日报只增加名称展示回退，不改变策略、存储格式或调度。

**Tech Stack:** Python 3.11、标准库 `json`/`html`、pytest、Ruff。

---

### Task 1: 占位活动名质量判定

**Files:**
- Modify: `src/adwatch/optimizer/ziniao.py`
- Modify: `src/adwatch/optimizer/collector.py`
- Test: `tests/optimizer/test_ziniao.py`
- Test: `tests/optimizer/test_collector.py`

- [ ] **Step 1: Write the failing tests**

为 `...`、`…`、`—`、`-`、空白和正常泰文活动名建立参数化契约；断言占位名被规范为空、`missing_fields` 包含 `campaign_name`、质量为 `degraded`，同时指标和稳定活动 ID 仍保留。

```python
@pytest.mark.parametrize("name", ["", "...", "…", "—", "-"])
def test_placeholder_campaign_name_is_missing_but_metric_is_preserved(name):
    page = _synthetic_page()
    page["campaign_rows"][0]["campaign_name"] = name
    result = ShopeeAdCollector(settings, FakePageReader(page)).collect(DAY)
    assert "campaign_name" in result.quality.missing_fields
    assert result.quality.status is DataQualityStatus.DEGRADED
    assert any(metric.campaign_id == "campaign-1" for metric in result.metrics)
```

- [ ] **Step 2: Run tests to verify RED**

Run: `.venv/bin/python -m pytest tests/optimizer/test_ziniao.py tests/optimizer/test_collector.py -q`

Expected: 新增用例因 `...` 未进入 `missing_fields` 而失败。

- [ ] **Step 3: Implement the minimal name normalization**

在 `ziniao.py` 增加单一名称规范化函数，只把完全由省略号或横线占位字符构成的文本判为空；解析快照和采集指标复用该函数。采集器在空名称时追加一次 `campaign_name`，但继续构造带稳定 ID 的指标与快照。

```python
_CAMPAIGN_NAME_PLACEHOLDER = re.compile(r"^[.。…·\-—–_]+$")

def normalize_campaign_name(value: object) -> str:
    text = _optional_text(value) or ""
    return "" if _CAMPAIGN_NAME_PLACEHOLDER.fullmatch(text) else text
```

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `.venv/bin/python -m pytest tests/optimizer/test_ziniao.py tests/optimizer/test_collector.py -q`

Expected: 全部通过。

### Task 2: 中文摘要与折叠审计详情

**Files:**
- Modify: `src/adwatch/optimizer/web.py`
- Test: `tests/optimizer/test_web.py`

- [ ] **Step 1: Write the failing Web tests**

建立包含 `policy_template`、`profile`、`snapshot` 中文备注和恶意 HTML 的真实
`Evidence`。断言默认区域包含中文单位摘要，不含 `\\u7531`，原始规则输入位于
闭合的 `<details><summary>技术审计详情</summary>` 中，且 HTML 仍被转义。

```python
assert "短窗 7 天、长窗 30 天" in page
assert "预算调整 10%（最多 20%）" in page
assert "最低 ROAS 5.5" in page
assert "当前预算 500 THB" in page
assert "\\u7531" not in page
assert "<summary>技术审计详情</summary>" in page
assert "&lt;script&gt;" in page
```

- [ ] **Step 2: Run the Web test to verify RED**

Run: `.venv/bin/python -m pytest tests/optimizer/test_web.py -q`

Expected: 新增断言失败，因为当前页面直接拼接原始键值。

- [ ] **Step 3: Implement safe summary rendering**

增加纯函数解析每个已知 JSON 值；合法 JSON 使用 `ensure_ascii=False` 格式化，
非法历史值原样转义。摘要只读取白名单字段并通过 `Decimal` 格式化百分比和金额；
原始输入进入默认折叠的 `<details>`，不执行或信任其中内容。

```python
def _decoded_rule_input(value: str) -> tuple[object | None, str]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None, value
    return parsed, json.dumps(parsed, ensure_ascii=False, sort_keys=True)
```

- [ ] **Step 4: Run Web tests to verify GREEN**

Run: `.venv/bin/python -m pytest tests/optimizer/test_web.py -q`

Expected: 全部通过。

### Task 3: 缺失名称展示回退与真实验证

**Files:**
- Modify: `src/adwatch/optimizer/web.py`
- Modify: `src/adwatch/optimizer/report.py`
- Test: `tests/optimizer/test_web.py`
- Test: `tests/optimizer/test_report.py`

- [ ] **Step 1: Write failing fallback tests**

断言空白或占位名称显示为 `未获取活动名称（<campaign_id>）`，正常名称保持原样，
Markdown 与 HTML 特殊字符继续安全转义。

- [ ] **Step 2: Run focused tests to verify RED**

Run: `.venv/bin/python -m pytest tests/optimizer/test_web.py tests/optimizer/test_report.py -q`

Expected: 占位名称仍直接显示，新增用例失败。

- [ ] **Step 3: Implement one presentation fallback per renderer**

Web 和 Markdown 渲染器各自使用小型纯函数判断无效名称，输出固定中文回退并附稳定
活动 ID；不回写数据库、不覆盖原始证据。

- [ ] **Step 4: Verify full suite and live latest day**

Run: `.venv/bin/python -m pytest -q`

Expected: 全部通过。

Run: `.venv/bin/python -m ruff check .`

Expected: `All checks passed!`

真实验证：运行 `.venv/bin/adwatch collect --date 2026-09-12`，再重跑
2026-09-13 日报；确认质量包含 `campaign_name`、Dashboard 不显示转义乱码，且
`schedule status` 仍为 `healthy`。

- [ ] **Step 5: Commit only the fix files**

```bash
git add src/adwatch/optimizer/ziniao.py src/adwatch/optimizer/collector.py \
  src/adwatch/optimizer/web.py src/adwatch/optimizer/report.py \
  tests/optimizer/test_ziniao.py tests/optimizer/test_collector.py \
  tests/optimizer/test_web.py tests/optimizer/test_report.py \
  docs/superpowers/plans/2026-09-13-evidence-display-and-placeholder-quality.md
git commit -m "fix: clarify evidence and detect placeholder campaign names"
```
