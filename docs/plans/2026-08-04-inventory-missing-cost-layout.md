# Inventory Missing Cost Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `/inventory` 的待补 SKU 成本宽表格改成无需横向滚动的紧凑清单，并通过单条展开表单完成成本补录。

**Architecture:** 保留现有 `InventoryService` 查询与 `/sku-costs` POST 路由，只修改服务端 HTML 渲染。每个 SKU 使用原生 `<details>/<summary>` 表达“摘要 + 展开编辑”，CSS 按 1024px、768px 两级重排，不引入 JavaScript 或新依赖。

**Tech Stack:** Python 3.12、服务端 HTML、原生 CSS、pytest、Ruff、应用内浏览器。

---

### Task 1：用渲染测试固定无横向滚动结构

**Files:**

- Modify: `tests/dashboard/test_views.py`
- Verify: `tests/dashboard/test_routes.py`

- [ ] **Step 1：在现有库存页测试中加入新结构断言**

在 `test_inventory_page_shows_missing_cost_and_in_page_write_forms` 末尾加入：

```python
assert 'class="missing-cost-list"' in page
assert page.count('class="missing-cost-item"') == 1
assert '<summary class="missing-cost-summary">' in page
assert 'class="missing-cost-editor"' in page
assert 'class="missing-cost-table"' not in page
assert "min-width:560px" not in page
assert "overflow-wrap:anywhere" in page
assert '<label for="sku-cost-0-effective-date">成本生效日期</label>' in page
assert '<label for="sku-cost-0-unit-cost">单位成本 (CNY)</label>' in page
assert '<label for="sku-cost-0-note">备注</label>' in page
```

- [ ] **Step 2：运行测试确认 RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/dashboard/test_views.py::test_inventory_page_shows_missing_cost_and_in_page_write_forms -q
```

Expected: FAIL，缺少 `missing-cost-list`，证明测试捕获的是尚未实现的新布局。

### Task 2：实现紧凑清单与展开编辑

**Files:**

- Modify: `src/adwatch/dashboard/views.py`
- Test: `tests/dashboard/test_views.py`

- [ ] **Step 1：增加单条 SKU 渲染函数**

在 `render_inventory_page` 前新增 `_render_missing_cost_item`。函数只负责 HTML 结构，继续转义全部数据库值：

```python
def _render_missing_cost_item(
    item: dict,
    *,
    index: int,
    csrf_token: str,
) -> str:
    prefix = f"sku-cost-{index}"
    seller_sku = html.escape(str(item["seller_sku"]))
    platform = html.escape(str(item["platform"]))
    store = html.escape(str(item["store"]))
    effective_date = html.escape(
        str(item["first_missing_date"]), quote=True
    )
    return f"""
    <details class="missing-cost-item">
      <summary class="missing-cost-summary">
        <span class="missing-cost-main">
          <strong class="missing-cost-sku">{seller_sku}</strong>
          <span class="missing-cost-context">{platform} · {store}</span>
        </span>
        <span class="missing-cost-metrics">
          <span><small>订单</small><b>{item['order_count']}</b></span>
          <span><small>缺成本件数</small><b>{item['missing_units']}</b></span>
          <span><small>最近订单</small><b>{html.escape(str(item['last_order_date']))}</b></span>
        </span>
        <span class="missing-cost-action">补录成本</span>
      </summary>
      <form method="post" action="/sku-costs" class="missing-cost-editor">
        <input type="hidden" name="csrf_token" value="{csrf_token}">
        <input type="hidden" name="platform" value="{platform}">
        <input type="hidden" name="store" value="{store}">
        <input type="hidden" name="seller_sku" value="{html.escape(str(item['seller_sku']), quote=True)}">
        <div class="form-field">
          <label for="{prefix}-effective-date">成本生效日期</label>
          <input id="{prefix}-effective-date" type="date" name="effective_date" value="{effective_date}" required>
        </div>
        <div class="form-field">
          <label for="{prefix}-unit-cost">单位成本 (CNY)</label>
          <input id="{prefix}-unit-cost" name="unit_cost_cny" inputmode="decimal" required>
        </div>
        <div class="form-field">
          <label for="{prefix}-note">备注</label>
          <input id="{prefix}-note" name="note" placeholder="选填">
        </div>
        <button type="submit" class="btn btn-success">保存成本</button>
      </form>
    </details>
    """
```

- [ ] **Step 2：用清单替换待补成本宽表格**

将 `missing_rows` 改为 `missing_items`：

```python
if missing_costs:
    token = html.escape(csrf_token, quote=True)
    missing_items = "".join(
        _render_missing_cost_item(
            item,
            index=index,
            csrf_token=token,
        )
        for index, item in enumerate(missing_costs)
    )
else:
    missing_items = (
        '<div class="empty-state">暂无待补 SKU 成本数据</div>'
    )
```

面板主体替换为：

```html
<div class="missing-cost-list">{missing_items}</div>
```

- [ ] **Step 3：添加响应式 CSS**

用以下核心规则替换 `.inline-cost-form` 固定宽度规则：

```css
.missing-cost-list { display:grid; gap:12px; min-width:0; }
.missing-cost-item {
  min-width:0; overflow:hidden; border:1px solid var(--color-border);
  border-radius:12px; background:var(--color-surface);
}
.missing-cost-summary {
  display:grid; grid-template-columns:minmax(0,1fr) minmax(360px,1.25fr) auto;
  align-items:center; gap:20px; min-width:0; padding:16px 18px;
  cursor:pointer; list-style:none; transition:background 180ms ease;
}
.missing-cost-summary::-webkit-details-marker { display:none; }
.missing-cost-summary:hover { background:var(--color-surface-hover); }
.missing-cost-summary:focus-visible {
  outline:3px solid color-mix(in srgb, var(--color-primary) 30%, transparent);
  outline-offset:-3px;
}
.missing-cost-main { display:grid; gap:4px; min-width:0; }
.missing-cost-sku { overflow-wrap:anywhere; }
.missing-cost-context { color:var(--color-text-muted); font-size:.82rem; }
.missing-cost-metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; }
.missing-cost-metrics span { display:grid; gap:2px; }
.missing-cost-metrics small { color:var(--color-text-muted); }
.missing-cost-metrics b { font-variant-numeric:tabular-nums; }
.missing-cost-action { color:var(--color-primary); font-weight:700; white-space:nowrap; }
.missing-cost-action::after { content:"展开"; margin-left:8px; font-size:.78rem; }
.missing-cost-item[open] .missing-cost-action::after { content:"收起"; }
.missing-cost-editor {
  display:grid; grid-template-columns:minmax(150px,.8fr) minmax(150px,.8fr) minmax(180px,1.2fr) auto;
  align-items:end; gap:12px; padding:16px 18px 18px;
  border-top:1px solid var(--color-border-light); background:var(--color-bg);
}
.missing-cost-editor .form-field { min-width:0; }
.missing-cost-editor input { width:100%; min-height:44px; }
.missing-cost-editor .btn { min-height:44px; }
@media (max-width:1024px) {
  .missing-cost-summary { grid-template-columns:minmax(0,1fr) auto; }
  .missing-cost-metrics { grid-column:1 / -1; }
  .missing-cost-editor { grid-template-columns:repeat(2,minmax(0,1fr)); }
}
@media (max-width:768px) {
  .missing-cost-summary, .missing-cost-editor { grid-template-columns:minmax(0,1fr); }
  .missing-cost-metrics { grid-template-columns:repeat(3,minmax(0,1fr)); }
  .missing-cost-editor .btn { width:100%; }
}
@media (prefers-reduced-motion:reduce) {
  .missing-cost-summary { transition:none; }
}
```

- [ ] **Step 4：运行目标测试确认 GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/dashboard/test_views.py tests/dashboard/test_routes.py -q
.venv/bin/python -m ruff check src/adwatch/dashboard/views.py tests/dashboard/test_views.py
```

Expected: 全部 PASS，Ruff 输出 `All checks passed!`。

### Task 3：响应式浏览器验收

**Files:**

- Verify: `src/adwatch/dashboard/views.py`
- Verify: `var/adwatch.sqlite3`

- [ ] **Step 1：运行全量质量门禁**

```bash
PYTHONPYCACHEPREFIX=/tmp/adwatch-inventory-ui .venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
PYTHONPYCACHEPREFIX=/tmp/adwatch-inventory-ui .venv/bin/python -m compileall -q src tests
git diff --check
```

Expected: pytest 0 failures、Ruff 0、compileall 0、diff check 0。

- [ ] **Step 2：重启 Dashboard 并检查真实数据**

打开 `http://127.0.0.1:8765/inventory?date=2026-08-03`，确认：

- 待补成本数量仍为 15。
- 页面宽度 1440、1024、768、375px 时 `document.documentElement.scrollWidth <= window.innerWidth`。
- 默认态不显示输入框；展开一条后只显示该条的编辑区。
- 成本日期、成本、备注和保存按钮均在视口内。
- `ST-OC1232-08A` 不出现。

- [ ] **Step 3：保留页面给用户验收**

将应用内浏览器恢复为用户当前窗口尺寸，停留在 `/inventory?date=2026-08-03`，不提交任何成本表单。

## 交付约束

- 不提交 Git；仓库规则要求用户明确授权后才能提交。
- 不修改数据库业务数据，不发送飞书，不启用 TikTok。
- 不重构 `/inventory` 其他模块。
