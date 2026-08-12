# Excel 原型公式等价收口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 AdWatch 中实现 Excel 原型从多币种明细到月度、年度和 60%/40% 分润的公式等价闭环，同时修正采购/销售成本和广告充值/实际消耗的重复扣减口径。

**Architecture:** 新建 `adwatch.accounting` 领域包，集中负责币种规范化、月度汇率解析、统一经营汇总和 Excel 预览；账本、库存和分润服务只负责写入及状态机，Dashboard 和报表只读取统一经营汇总结果。所有已确认金额保存原币、采用汇率和人民币快照，月度汇率修改不静默改写历史事实。

**Tech Stack:** Python 3.12、SQLite 迁移、`Decimal`、标准库 `dataclasses`、现有 `openpyxl`、服务端 HTML、pytest、Ruff、Docker Compose。

---

## 0. 文件结构与实施边界

### 新建文件

- `src/adwatch/accounting/__init__.py`：导出会计领域公共类型。
- `src/adwatch/accounting/money.py`：币种规范化、金额换算和舍入。
- `src/adwatch/accounting/exchange_rates.py`：月度覆盖维护和确定性汇率解析。
- `src/adwatch/accounting/operating_statement.py`：唯一经营损益/月度/年度汇总服务。
- `src/adwatch/accounting/excel_preview.py`：Excel 原型历史数据只读预览与重复检测。
- `tests/accounting/test_money.py`：金额规则。
- `tests/accounting/test_exchange_rates.py`：汇率优先级与冻结规则。
- `tests/accounting/test_operating_statement.py`：公式等价、现金/损益分账。
- `tests/accounting/test_excel_preview.py`：11 张表识别、校验与查重。
- `tests/storage/test_excel_formula_parity_migration.py`：迁移 40/41 结构和旧数据兼容。

### 修改文件

- `src/adwatch/storage/migrations.py`：迁移 40/41。
- `src/adwatch/ledger/models.py`：多币种输入值对象。
- `src/adwatch/ledger/service.py`：收入、广告资金、刷单、出资的多币种写入和读取。
- `src/adwatch/inventory/models.py`：采购原币及物流字段。
- `src/adwatch/inventory/service.py`：采购换算、现金流和出库查询。
- `src/adwatch/profit_sharing/service.py`：统一汇总、非负可分利润、逐人余额和状态。
- `src/adwatch/dashboard/routes.py`：汇率、收入、采购、资金、刷单、出资路由。
- `src/adwatch/dashboard/views.py`：月度经营账、原币表单、物流、分润余额。
- `src/adwatch/dashboard/app.py`：`/operating-statement` 页面和年份查询。
- `src/adwatch/reporting/read_model.py`、`src/adwatch/reporting/markdown.py`：复用统一经营汇总。
- `src/adwatch/cli.py`：Excel 预览命令；不提供未经确认的导入写命令。
- `README.md`、`docs/operations/data-foundation.md`、`docs/deploy/README.md`：口径和操作说明。

### 不做

- 不把 Excel 历史数据自动写入数据库；
- 不恢复 TikTok 自动采集；
- 不开启 Live 广告写操作；
- 不删除或重写平台原始事实；
- 不把采购付款或广告充值计入经营净利润。

## Task 0: 恢复隔离分支绿色基线

**Files:**
- Inspect only: 主工作区所有已跟踪修改
- Merge/Cherry-pick: 经审查的现有修复提交
- Test: `tests/`

- [ ] **Step 1: 在主工作区记录当前差异和质量证据**

Run:

```bash
git status --short
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
.venv/bin/python -m compileall -q src tests
git diff --check
```

Expected: 主工作区测试为当前已知绿色基线（最近证据为 619 passed），Ruff、compileall 和 diff check 通过；若失败，只修复既有基线，不开始本计划功能。

- [ ] **Step 2: 将既有已批准修复按范围形成基线提交**

只暂存已审查的源码、测试、配置和说明文件；明确排除 `.env`、`.DS_Store`、`tmp/`、`outputs/`、数据库、日志和凭证：

```bash
git add .dockerignore .env.example Dockerfile README.md VERSION docker-compose.yml
git add src tests
git add docs task_plan.md findings.md progress.md
git diff --cached --check
git status --short
git commit -m "fix: preserve verified AdWatch production baseline"
```

Expected: 提交不包含 `.env`、数据库、日志、凭证、原型 Excel、`tmp/` 或 `outputs/`。

- [ ] **Step 3: 将基线提交同步到隔离分支**

Run in this worktree:

```bash
git merge --ff-only main
```

Expected: `codex/excel-formula-parity` 快进到包含迁移 39 和全部现有修复的主分支；若不能快进，停止并检查提交图，不使用 `reset --hard`。

- [ ] **Step 4: 验证隔离分支绿色**

Run:

```bash
../../.venv/bin/python -m pytest -q
../../.venv/bin/python -m ruff check src tests
../../.venv/bin/python -m compileall -q src tests
git diff --check
```

Expected: 全部通过，且 `git status --short` 只包含本计划文档；不接受当前过期分支的 `9 failed, 583 passed` 作为开发基线。

## Task 1: 金额值对象、月度汇率覆盖与兼容迁移

**Files:**
- Create: `src/adwatch/accounting/__init__.py`
- Create: `src/adwatch/accounting/money.py`
- Create: `src/adwatch/accounting/exchange_rates.py`
- Modify: `src/adwatch/storage/migrations.py`
- Test: `tests/accounting/test_money.py`
- Test: `tests/accounting/test_exchange_rates.py`
- Test: `tests/storage/test_excel_formula_parity_migration.py`

- [ ] **Step 1: 写币种规范化和换算失败测试**

```python
from decimal import Decimal
import pytest

from adwatch.accounting.money import MoneyInputError, convert_to_cny, normalize_currency


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("¥", "CNY"), ("￥", "CNY"), ("cny", "CNY"), ("฿", "THB"), ("thb", "THB"), ("S$", "SGD")],
)
def test_normalize_excel_currency_symbols(raw, expected):
    assert normalize_currency(raw) == expected


def test_convert_to_cny_uses_decimal_and_half_up_rounding():
    assert convert_to_cny(Decimal("10.005"), Decimal("1")) == Decimal("10.01")
    assert convert_to_cny(Decimal("100"), Decimal("0.2034")) == Decimal("20.34")


def test_unknown_currency_and_non_positive_rate_are_rejected():
    with pytest.raises(MoneyInputError, match="unsupported currency"):
        normalize_currency("USD")
    with pytest.raises(MoneyInputError, match="positive"):
        convert_to_cny(Decimal("10"), Decimal("0"))
```

- [ ] **Step 2: 运行金额测试确认 RED**

Run: `../../.venv/bin/python -m pytest tests/accounting/test_money.py -q`

Expected: FAIL，提示 `adwatch.accounting` 不存在。

- [ ] **Step 3: 实现金额值对象**

```python
# src/adwatch/accounting/money.py
from decimal import ROUND_HALF_UP, Decimal


class MoneyInputError(ValueError):
    pass


_CURRENCIES = {
    "¥": "CNY", "￥": "CNY", "CNY": "CNY",
    "฿": "THB", "THB": "THB",
    "S$": "SGD", "SGD": "SGD",
}


def normalize_currency(raw: str) -> str:
    normalized = _CURRENCIES.get(raw.strip().upper())
    if normalized is None:
        raise MoneyInputError(f"unsupported currency: {raw}")
    return normalized


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def convert_to_cny(amount_original: Decimal, rate_to_cny: Decimal) -> Decimal:
    if amount_original < 0 or rate_to_cny <= 0:
        raise MoneyInputError("amount must be non-negative and rate must be positive")
    return money(amount_original * rate_to_cny)
```

`src/adwatch/accounting/__init__.py` 导出 `MoneyInputError`、`convert_to_cny`、`money` 和 `normalize_currency`。

- [ ] **Step 4: 写迁移 40 失败测试**

```python
def test_migration_40_adds_formula_parity_columns_and_monthly_rates(tmp_path):
    database = Database(tmp_path / "formula-parity.sqlite3")
    database.migrate()
    with database.connect() as connection:
        latest = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        funding = {row[1] for row in connection.execute("PRAGMA table_info(ad_funding_entries)")}
        capital = {row[1] for row in connection.execute("PRAGMA table_info(capital_entries)")}
        review = {row[1] for row in connection.execute("PRAGMA table_info(review_order_costs)")}
        receipts = {row[1] for row in connection.execute("PRAGMA table_info(purchase_receipts)")}
        lines = {row[1] for row in connection.execute("PRAGMA table_info(purchase_lines)")}
    assert latest >= 40
    assert "monthly_exchange_rate_overrides" in tables
    assert {"credited_amount_original", "credited_amount_cny", "rate_date", "rate_source"} <= funding
    assert {"entry_name", "currency", "rate_to_cny", "rate_date", "rate_source", "note"} <= capital
    assert {"goods_cost_original", "service_fee_original", "currency", "rate_to_cny"} <= review
    assert {"currency", "rate_to_cny", "rate_date", "rate_source", "tracking_number", "arrival_status", "note"} <= receipts
    assert {"product_name", "unit_cost_original", "line_cost_original"} <= lines
```

- [ ] **Step 5: 运行迁移测试确认 RED**

Run: `../../.venv/bin/python -m pytest tests/storage/test_excel_formula_parity_migration.py -q`

Expected: FAIL，缺少迁移 40 和字段。

- [ ] **Step 6: 增加迁移 40**

在 `MIGRATIONS` 尾部增加版本 40。核心 SQL 必须包含：

```sql
CREATE TABLE monthly_exchange_rate_overrides (
    currency TEXT NOT NULL,
    year_month TEXT NOT NULL CHECK(length(year_month)=7),
    rate_to_cny TEXT NOT NULL,
    source TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(currency, year_month)
);
ALTER TABLE ad_funding_entries ADD COLUMN credited_amount_original TEXT;
ALTER TABLE ad_funding_entries ADD COLUMN credited_amount_cny TEXT;
ALTER TABLE ad_funding_entries ADD COLUMN rate_date TEXT;
ALTER TABLE ad_funding_entries ADD COLUMN rate_source TEXT NOT NULL DEFAULT 'legacy';
UPDATE ad_funding_entries SET credited_amount_original=amount_original,
    credited_amount_cny=amount_cny, rate_date=occurred_on;
ALTER TABLE capital_entries ADD COLUMN entry_name TEXT NOT NULL DEFAULT '';
ALTER TABLE capital_entries ADD COLUMN rate_date TEXT;
ALTER TABLE capital_entries ADD COLUMN rate_source TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE capital_entries ADD COLUMN note TEXT NOT NULL DEFAULT '';
UPDATE capital_entries SET rate_date=occurred_on;
ALTER TABLE review_order_costs ADD COLUMN goods_cost_original TEXT;
ALTER TABLE review_order_costs ADD COLUMN service_fee_original TEXT;
ALTER TABLE review_order_costs ADD COLUMN currency TEXT NOT NULL DEFAULT 'CNY';
ALTER TABLE review_order_costs ADD COLUMN rate_to_cny TEXT NOT NULL DEFAULT '1';
ALTER TABLE review_order_costs ADD COLUMN rate_date TEXT;
ALTER TABLE review_order_costs ADD COLUMN rate_source TEXT NOT NULL DEFAULT 'legacy';
UPDATE review_order_costs SET goods_cost_original=goods_cost_cny,
    service_fee_original=service_fee_cny, rate_date=occurred_on;
ALTER TABLE purchase_receipts ADD COLUMN currency TEXT NOT NULL DEFAULT 'CNY';
ALTER TABLE purchase_receipts ADD COLUMN rate_to_cny TEXT NOT NULL DEFAULT '1';
ALTER TABLE purchase_receipts ADD COLUMN rate_date TEXT;
ALTER TABLE purchase_receipts ADD COLUMN rate_source TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE purchase_receipts ADD COLUMN tracking_number TEXT NOT NULL DEFAULT '';
ALTER TABLE purchase_receipts ADD COLUMN arrival_status TEXT NOT NULL DEFAULT 'received';
ALTER TABLE purchase_receipts ADD COLUMN note TEXT NOT NULL DEFAULT '';
UPDATE purchase_receipts SET rate_date=received_on;
ALTER TABLE purchase_lines ADD COLUMN product_name TEXT NOT NULL DEFAULT '';
ALTER TABLE purchase_lines ADD COLUMN unit_cost_original TEXT;
ALTER TABLE purchase_lines ADD COLUMN line_cost_original TEXT;
UPDATE purchase_lines SET unit_cost_original=unit_cost_cny,
    line_cost_original=line_cost_cny;
```

- [ ] **Step 7: 写月度汇率优先级失败测试**

```python
def test_monthly_override_precedes_exact_and_prior_daily_rate(tmp_path):
    database = migrated_database(tmp_path)
    with database.transaction() as connection:
        connection.execute("INSERT INTO exchange_rates(currency, rate_date, rate_to_cny) VALUES ('THB','2026-08-01','0.20')")
        connection.execute("INSERT INTO exchange_rates(currency, rate_date, rate_to_cny) VALUES ('THB','2026-08-05','0.21')")
    service = AccountingExchangeRateService(database, max_staleness_days=7)
    assert service.resolve("THB", date(2026, 8, 5)).rate == Decimal("0.21")
    service.set_monthly_override(currency="THB", year_month="2026-08", rate_to_cny=Decimal("0.22"), source="manual", note="月结汇率", actor="yl")
    resolution = service.resolve("฿", date(2026, 8, 5))
    assert (resolution.rate, resolution.source, resolution.source_date) == (Decimal("0.22"), "monthly_override", date(2026, 8, 1))


def test_cny_is_one_and_missing_or_stale_rate_is_not_zero(tmp_path):
    service = AccountingExchangeRateService(migrated_database(tmp_path), max_staleness_days=7)
    assert service.resolve("¥", date(2026, 8, 5)).rate == Decimal("1")
    with pytest.raises(ExchangeRateInputError, match="no THB/CNY"):
        service.resolve("THB", date(2026, 8, 5))
```

- [ ] **Step 8: 实现 `AccountingExchangeRateService`**

实现以下稳定接口：

```python
@dataclass(frozen=True)
class AccountingRateResolution:
    currency: str
    data_date: date
    rate: Decimal
    source: str
    source_date: date


class AccountingExchangeRateService:
    def set_monthly_override(self, *, currency: str, year_month: str, rate_to_cny: Decimal, source: str, note: str, actor: str) -> None: ...
    def resolve(self, currency: str, data_date: date) -> AccountingRateResolution: ...
    def list_monthly_rates(self, year: int) -> tuple[dict, ...]: ...
```

`resolve()` 按设计使用“月度覆盖 → 精确日 → 最近日 → 报错”，不得联网、不得写回日汇率表、不得把缺失值当 0 或 1。

- [ ] **Step 9: 运行 Task 1 测试并提交**

Run:

```bash
../../.venv/bin/python -m pytest tests/accounting/test_money.py tests/accounting/test_exchange_rates.py tests/storage/test_excel_formula_parity_migration.py -q
../../.venv/bin/python -m ruff check src/adwatch/accounting tests/accounting tests/storage/test_excel_formula_parity_migration.py
git add src/adwatch/accounting src/adwatch/storage/migrations.py tests/accounting tests/storage/test_excel_formula_parity_migration.py
git commit -m "feat: add accounting money and monthly exchange rates"
```

Expected: 全部通过。

## Task 2: 多币种收入、广告资金、刷单和前期投入

**Files:**
- Modify: `src/adwatch/storage/migrations.py`
- Modify: `src/adwatch/ledger/models.py`
- Modify: `src/adwatch/ledger/service.py`
- Modify: `src/adwatch/dashboard/routes.py`
- Test: `tests/storage/test_excel_formula_parity_migration.py`
- Test: `tests/ledger/test_service.py`
- Test: `tests/dashboard/test_routes.py`

- [ ] **Step 1: 写迁移 41 和结构化人工收入失败测试**

```python
def test_migration_41_creates_manual_income_entries(tmp_path):
    database = Database(tmp_path / "manual-income.sqlite3")
    database.migrate()
    with database.connect() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(manual_income_entries)")}
    assert {"platform", "store", "order_id", "occurred_on", "amount_original", "currency", "rate_to_cny", "amount_cny", "rate_date", "rate_source", "status", "note", "created_by"} <= columns
```

迁移 41 建表：

```sql
CREATE TABLE manual_income_entries (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    store TEXT NOT NULL,
    order_id TEXT NOT NULL,
    occurred_on TEXT NOT NULL,
    amount_original TEXT NOT NULL,
    currency TEXT NOT NULL,
    rate_to_cny TEXT NOT NULL,
    amount_cny TEXT NOT NULL,
    rate_date TEXT NOT NULL,
    rate_source TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('confirmed','reversed')),
    note TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reversal_reason TEXT,
    UNIQUE(platform, store, order_id, occurred_on, amount_original, currency)
);
```

- [ ] **Step 2: 写四类多币种账本失败测试**

```python
def test_multicurrency_income_funding_review_and_capital_freeze_rate(service):
    ledger, database = service
    income = ledger.create_manual_income(ManualIncomeDraft(
        platform="shopee", store="shop", order_id="O-1",
        occurred_on=date(2026, 8, 5), amount_original=Decimal("100"),
        currency="SGD", rate_to_cny=Decimal("5.60"),
        rate_date=date(2026, 8, 1), rate_source="monthly_override", note="结算补录",
    ), actor="yl")
    funding = ledger.create_ad_funding(AdFundingDraft(
        platform="shopee", store="shop", entry_type="recharge",
        paid_amount_original=Decimal("642"), credited_amount_original=Decimal("600"),
        currency="THB", rate_to_cny=Decimal("0.20"), occurred_on=date(2026, 8, 5),
        rate_date=date(2026, 8, 1), rate_source="monthly_override", source="manual",
    ), actor="yl")
    review = ledger.create_review_order_cost(ReviewCostDraft(
        platform="shopee", store="shop", order_id="R-1", seller_sku="SKU-1",
        goods_cost_original=Decimal("190.93"), service_fee_original=Decimal("13"),
        currency="CNY", rate_to_cny=Decimal("1"), occurred_on=date(2026, 8, 5),
        rate_date=date(2026, 8, 5), rate_source="identity",
    ), actor="yl")
    capital = ledger.create_capital(CapitalDraft(
        partner="洁云", entry_type="paid_in", entry_name="虾皮下店",
        amount_original=Decimal("450"), currency="CNY", rate_to_cny=Decimal("1"),
        occurred_on=date(2026, 8, 5), rate_date=date(2026, 8, 5),
        rate_source="identity", note="",
    ), actor="yl")
    assert all((income, funding, review, capital))
    with database.connect() as connection:
        assert connection.execute("SELECT amount_cny FROM manual_income_entries").fetchone()[0] == "560.00"
        assert tuple(connection.execute("SELECT amount_cny, credited_amount_cny FROM ad_funding_entries").fetchone()) == ("128.40", "120.00")
        assert tuple(connection.execute("SELECT goods_cost_cny, service_fee_cny FROM review_order_costs").fetchone()) == ("190.93", "13.00")
        assert connection.execute("SELECT amount_cny FROM capital_entries").fetchone()[0] == "450.00"
```

- [ ] **Step 3: 运行目标测试确认 RED**

Run: `../../.venv/bin/python -m pytest tests/ledger/test_service.py tests/storage/test_excel_formula_parity_migration.py -q`

Expected: FAIL，缺少 Draft 类型、迁移 41 和新签名。

- [ ] **Step 4: 增加不可变 Draft 类型**

在 `ledger/models.py` 定义：

```python
@dataclass(frozen=True)
class ManualIncomeDraft:
    platform: str; store: str; order_id: str; occurred_on: date
    amount_original: Decimal; currency: str; rate_to_cny: Decimal
    rate_date: date; rate_source: str; note: str = ""

@dataclass(frozen=True)
class AdFundingDraft:
    platform: str; store: str; entry_type: str
    paid_amount_original: Decimal; credited_amount_original: Decimal
    currency: str; rate_to_cny: Decimal; occurred_on: date
    rate_date: date; rate_source: str; source: str

@dataclass(frozen=True)
class ReviewCostDraft:
    platform: str; store: str; order_id: str; seller_sku: str
    goods_cost_original: Decimal; service_fee_original: Decimal
    currency: str; rate_to_cny: Decimal; occurred_on: date
    rate_date: date; rate_source: str

@dataclass(frozen=True)
class CapitalDraft:
    partner: str; entry_type: str; entry_name: str
    amount_original: Decimal; currency: str; rate_to_cny: Decimal
    occurred_on: date; rate_date: date; rate_source: str; note: str = ""
```

- [ ] **Step 5: 实现账本写入、现金规则和兼容包装**

所有 Draft 先调用 `normalize_currency()`、`convert_to_cny()`；旧公开签名保留为 CNY 兼容包装，避免现有调用方回归。现金规则：

```text
manual income confirmed -> +amount_cny
ad recharge -> -paid_amount_cny
ad gift -> 0 cash, +credited balance
ad refund -> +paid_amount_cny
review cost -> -(goods_cost_cny + service_fee_cny)
capital -> +amount_cny
```

反向操作继续追加等额反向现金事实，不删除原行。列表 API 返回原币、汇率、汇率日期/来源和人民币金额。

- [ ] **Step 6: 写路由自动解析汇率失败测试**

```python
def test_manual_income_and_multicurrency_routes_resolve_monthly_rate(tmp_path):
    database = Database(tmp_path / "web.sqlite3")
    database.migrate()
    AccountingExchangeRateService(database, 7).set_monthly_override(
        currency="SGD", year_month="2026-08", rate_to_cny=Decimal("5.6"),
        source="manual", note="", actor="yl",
    )
    router = DashboardRouter(LedgerService(database), csrf_token="secret", database=database)
    response = router.post("/manual-income", {
        "csrf_token": "secret", "occurred_on": "2026-08-05", "platform": "shopee",
        "store": "shop", "order_id": "O-1", "amount_original": "100",
        "currency": "SGD", "note": "",
    })
    assert response.status == 303
    with database.connect() as connection:
        assert connection.execute("SELECT amount_cny FROM manual_income_entries").fetchone()[0] == "560.00"
```

- [ ] **Step 7: 实现路由并验证**

路由在未提供显式 `rate_to_cny` 时调用 `AccountingExchangeRateService.resolve()`；若提供行级汇率，必须同时提供 `rate_reason`，将来源保存为 `manual_line_override`。缺汇率返回 400，不写任何事实。

Run:

```bash
../../.venv/bin/python -m pytest tests/ledger/test_service.py tests/dashboard/test_routes.py tests/storage/test_excel_formula_parity_migration.py -q
../../.venv/bin/python -m ruff check src/adwatch/ledger src/adwatch/dashboard/routes.py tests/ledger tests/dashboard/test_routes.py
git add src/adwatch/storage/migrations.py src/adwatch/ledger src/adwatch/dashboard/routes.py tests/storage/test_excel_formula_parity_migration.py tests/ledger tests/dashboard/test_routes.py
git commit -m "feat: add multicurrency operating ledger entries"
```

## Task 3: 多币种采购、物流和出库明细

**Files:**
- Modify: `src/adwatch/inventory/models.py`
- Modify: `src/adwatch/inventory/service.py`
- Modify: `src/adwatch/dashboard/routes.py`
- Modify: `src/adwatch/dashboard/views.py`
- Test: `tests/inventory/test_service.py`
- Test: `tests/dashboard/test_routes.py`
- Test: `tests/dashboard/test_views.py`

- [ ] **Step 1: 写采购公式和物流失败测试**

```python
def test_purchase_converts_original_unit_cost_and_preserves_logistics(inventory):
    service, database = inventory
    service.receive_purchase(
        receipt_id="PO-THB-1", supplier="factory", received_on=date(2026, 8, 5),
        currency="THB", rate_to_cny=Decimal("0.20"), rate_date=date(2026, 8, 1),
        rate_source="monthly_override", tracking_number="TRACK-1",
        arrival_status="in_transit", note="首批",
        lines=(PurchaseLine("SKU-1", 3, Decimal("50"), product_name="泡脚包"),), actor="yl",
    )
    with database.connect() as connection:
        receipt = connection.execute("SELECT currency, rate_to_cny, tracking_number, arrival_status FROM purchase_receipts").fetchone()
        line = connection.execute("SELECT unit_cost_original, line_cost_original, unit_cost_cny, line_cost_cny FROM purchase_lines").fetchone()
    assert tuple(receipt) == ("THB", "0.20", "TRACK-1", "in_transit")
    assert tuple(line) == ("50", "150", "10.00", "30.00")
    assert service.balance("SKU-1") == 3
```

- [ ] **Step 2: 扩展 `PurchaseLine` 和 `receive_purchase()`**

```python
@dataclass(frozen=True)
class PurchaseLine:
    seller_sku: str
    quantity: int
    unit_cost_original: Decimal
    product_name: str = ""
```

服务用采购单汇率换算每行，保存原币和 CNY。旧调用 `PurchaseLine(sku, qty, cny)` 默认由 `receive_purchase()` 的 `currency='CNY'`、`rate_to_cny=1` 保持兼容。

- [ ] **Step 3: 写出库查询失败测试**

```python
def test_list_order_shipments_joins_order_cost_and_logistics(inventory):
    service, database = inventory
    seed_order_cost_and_logistics(database, tracking_number="TRACK-O-1")
    assert service.list_order_shipments() == ({
        "order_date": "2026-08-05", "platform": "shopee", "store": "shop",
        "order_id": "O-1", "product_name": "泡脚包", "seller_sku": "SKU-1",
        "quantity": 2, "unit_cost_cny": Decimal("10"), "total_cost_cny": Decimal("20"),
        "tracking_number": "TRACK-O-1", "logistics_status": "shipped",
    },)
```

- [ ] **Step 4: 实现只读出库查询**

查询 `effective_platform_order_lines`、`order_cost_snapshots`、
`platform_logistics_observations`，以平台、规范店铺、订单和 SKU 对齐；无物流事实时回退订单行 `logistics_status`，物流单号展示空字符串。不得因列表查询创建或修改库存事实。

- [ ] **Step 5: 扩展采购路由和库存页面**

采购表单增加：产品名称、原币单位价、币种、汇率建议、物流单号、到货状态、备注。出库表格增加：订单、产品、SKU、数量、单位成本、总成本、物流单号和发货状态。服务端始终重算人民币金额，不接受浏览器提交 `line_cost_cny`。

- [ ] **Step 6: 运行 Task 3 测试并提交**

```bash
../../.venv/bin/python -m pytest tests/inventory/test_service.py tests/dashboard/test_routes.py tests/dashboard/test_views.py -q
../../.venv/bin/python -m ruff check src/adwatch/inventory src/adwatch/dashboard tests/inventory tests/dashboard
git add src/adwatch/inventory src/adwatch/dashboard tests/inventory tests/dashboard
git commit -m "feat: preserve purchase currency and shipment details"
```

## Task 4: 唯一经营汇总服务和 Excel 公式等价

**Files:**
- Create: `src/adwatch/accounting/operating_statement.py`
- Test: `tests/accounting/test_operating_statement.py`
- Modify: `src/adwatch/profit_sharing/service.py`
- Modify: `src/adwatch/reporting/read_model.py`
- Test: `tests/profit_sharing/test_service.py`
- Test: `tests/reporting/test_read_model.py`

- [ ] **Step 1: 写月度公式等价失败测试**

```python
def test_monthly_statement_separates_cash_from_profit(database):
    seed_accounting_month(
        database,
        settlement=Decimal("1000"), income_adjustment=Decimal("50"),
        purchase_payment=Decimal("300"), cogs=Decimal("250"),
        platform_fee=Decimal("80"), ad_recharge_paid=Decimal("500"),
        ad_recharge_credited=Decimal("470"), ad_spend=Decimal("100"),
        operating_expense=Decimal("40"), review_cost=Decimal("30"),
        capital_in=Decimal("600"), withdrawal=Decimal("50"),
    )
    statement = OperatingStatementService(database).monthly(2026, 8)
    assert statement.revenue_cny == Decimal("1050.00")
    assert statement.purchase_cash_cny == Decimal("300.00")
    assert statement.cogs_cny == Decimal("250.00")
    assert statement.ad_recharge_paid_cny == Decimal("500.00")
    assert statement.ad_recharge_credited_cny == Decimal("470.00")
    assert statement.ad_spend_cny == Decimal("100.00")
    assert statement.net_profit_cny == Decimal("550.00")
    assert statement.capital_in_cny == Decimal("600.00")
    assert statement.withdrawal_cny == Decimal("50.00")
```

净利润断言必须严格等于：`1050 - 250 - 80 - 100 - 40 - 30 = 550`；采购 300 和充值 500 不得再次扣减。

- [ ] **Step 2: 写状态过滤和缺失数据失败测试**

```python
def test_statement_excludes_drafts_reversed_facts_and_reports_missing_inputs(database):
    seed_draft_reversed_and_missing_facts(database)
    statement = OperatingStatementService(database).monthly(2026, 8)
    assert statement.net_profit_cny is None
    assert statement.completeness == "pending_data"
    assert set(statement.missing_inputs) == {"product_cost", "refunds"}
    assert statement.source_counts["expense_entries"] == 0
```

- [ ] **Step 3: 定义稳定返回类型并实现服务**

```python
@dataclass(frozen=True)
class OperatingStatement:
    starts_on: date
    ends_on: date
    revenue_cny: Decimal
    purchase_cash_cny: Decimal
    cogs_cny: Decimal | None
    platform_fee_cny: Decimal
    ad_recharge_paid_cny: Decimal
    ad_recharge_credited_cny: Decimal
    ad_spend_cny: Decimal
    review_cost_cny: Decimal
    operating_expense_cny: Decimal
    net_profit_cny: Decimal | None
    capital_in_cny: Decimal
    withdrawal_cny: Decimal
    operating_cash_change_cny: Decimal
    completeness: str
    missing_inputs: tuple[str, ...]
    source_counts: dict[str, int]


class OperatingStatementService:
    def between(self, starts_on: date, ends_on: date) -> OperatingStatement: ...
    def monthly(self, year: int, month: int) -> OperatingStatement: ...
    def yearly(self, year: int) -> tuple[OperatingStatement, ...]: ...
```

实现时集中定义有效状态过滤：平台结算、已确认人工收入、已确认订单成本快照、
当前版本平台费、实际广告消耗、已确认且计利润费用、已确认刷单。现金变化单独
使用结算/人工收入、采购付款、广告充值、费用、刷单、投入、提款和分润支付。

- [ ] **Step 4: 用统一服务替换分润重复 SQL**

`ProfitSharingService.create_period_from_ledger()` 只调用：

```python
statement = OperatingStatementService(self.database).between(starts_on, ends_on)
if statement.net_profit_cny is None:
    raise ProfitSharingError("profit period has pending accounting inputs")
return self.create_period(
    starts_on=starts_on, ends_on=ends_on,
    net_profit_cny=statement.net_profit_cny, actor=actor,
)
```

`calculate_net_profit_breakdown()` 从同一对象映射，不保留第二套 SQL。

- [ ] **Step 5: 让月报读模型复用统一汇总**

广告月报现有 Campaign/ROAS 统计保留，但经营利润卡和经营费用拆解改从
`OperatingStatementService.monthly()` 获取；加入测试证明月报和分润同一月份
净利润一致。

- [ ] **Step 6: 运行 Task 4 测试并提交**

```bash
../../.venv/bin/python -m pytest tests/accounting/test_operating_statement.py tests/profit_sharing/test_service.py tests/reporting/test_read_model.py -q
../../.venv/bin/python -m ruff check src/adwatch/accounting/operating_statement.py src/adwatch/profit_sharing src/adwatch/reporting tests/accounting tests/profit_sharing tests/reporting
git add src/adwatch/accounting/operating_statement.py src/adwatch/profit_sharing src/adwatch/reporting tests/accounting/test_operating_statement.py tests/profit_sharing tests/reporting
git commit -m "feat: centralize monthly operating statements"
```

## Task 5: 本月、12 个月和年度经营账页面

**Files:**
- Modify: `src/adwatch/dashboard/app.py`
- Modify: `src/adwatch/dashboard/views.py`
- Test: `tests/dashboard/test_app.py`
- Test: `tests/dashboard/test_views.py`

- [ ] **Step 1: 写经营账页面失败测试**

```python
def test_operating_statement_page_renders_twelve_months_and_correct_formula(database):
    seed_statement_month(database, year=2026, month=8)
    html = render_operating_statement_page(database, year=2026, csrf_token="secret")
    assert "月度经营账" in html
    assert html.count('data-month="2026-') == 12
    assert "采购现金支出" in html
    assert "广告实际消耗" in html
    assert "广告充值不计入利润" in html
    assert "年度合计" in html


def test_dashboard_contains_current_month_year_and_rate_cards(database):
    seed_statement_month(database, year=2026, month=8)
    html = render_dashboard(database, date(2026, 8, 12), simulated=False).decode()
    assert "本月经营" in html
    assert "年度经营" in html
    assert "SGD → CNY" in html
    assert "/operating-statement?year=2026" in html
```

- [ ] **Step 2: 实现 `/operating-statement?year=YYYY`**

在 `NAVIGATION` 加入 `("/operating-statement", "月度经营账")`。GET 解析 `year`，
只接受 2000–2100；非法值返回 400。页面表格固定 12 行，列为收入、采购现金、
销售成本、平台费、广告充值支付/到账、广告实际消耗、刷单、经营费用、净利润、
完整性。年度合计由 12 个 `OperatingStatement` 字段求和；任何月份净利润缺失时，
年度净利润显示“待补数据”，不得把缺失当 0。

- [ ] **Step 3: 在今日经营加入公式等价卡片**

追加本月收入、销售成本、实际广告消耗、经营费用、净利润；年度收入、成本、
费用、净利润；当前月份 THB→CNY、CNY→THB（倒数展示）、SGD→CNY。保留原有
今日广告趋势，不改变其数据日期选择行为。

- [ ] **Step 4: 运行页面测试并提交**

```bash
../../.venv/bin/python -m pytest tests/dashboard/test_app.py tests/dashboard/test_views.py -q
../../.venv/bin/python -m ruff check src/adwatch/dashboard tests/dashboard
git add src/adwatch/dashboard tests/dashboard
git commit -m "feat: add monthly and annual operating statement views"
```

## Task 6: 月度汇率和多币种 Web 录入闭环

**Files:**
- Modify: `src/adwatch/dashboard/routes.py`
- Modify: `src/adwatch/dashboard/views.py`
- Test: `tests/dashboard/test_routes.py`
- Test: `tests/dashboard/test_views.py`

- [ ] **Step 1: 写月度汇率 POST 失败测试**

```python
def test_monthly_exchange_rate_route_persists_override(tmp_path):
    database = Database(tmp_path / "web.sqlite3")
    database.migrate()
    router = dashboard_router(database, csrf="secret")
    response = router.post("/monthly-exchange-rates", {
        "csrf_token": "secret", "currency": "THB", "year_month": "2026-08",
        "rate_to_cny": "0.2034", "source": "manual", "note": "月结",
    })
    assert response.status == 303
    assert response.location == "/operations"
    with database.connect() as connection:
        assert tuple(connection.execute("SELECT currency, year_month, rate_to_cny FROM monthly_exchange_rate_overrides").fetchone()) == ("THB", "2026-08", "0.2034")
```

- [ ] **Step 2: 写表单字段和汇率只读预览失败测试**

```python
def test_operations_and_funds_forms_expose_original_currency_fields(database):
    operations = render_operations_page(database, date(2026, 8, 12), "secret")
    funds = render_ad_funds_page(database, date(2026, 8, 12), "secret")
    for field in ("year_month", "rate_to_cny", "goods_cost_original", "service_fee_original", "entry_name"):
        assert f'name="{field}"' in operations
    for field in ("amount_original", "credited_amount_original", "currency"):
        assert f'name="{field}"' in funds
```

- [ ] **Step 3: 实现汇率维护和多币种表单**

`/operations` 改为真实数据库驱动页面，展示指定年份月度覆盖和有效日汇率。
费用、收入、采购、广告资金、刷单、出资的币种统一使用 CNY/THB/SGD 下拉。
表单不提交计算后的人民币金额；服务端解析并在 303 后的列表中显示原币、汇率、
人民币金额和汇率来源。

- [ ] **Step 4: 验证缺汇率和无理由行级覆盖**

新增断言：缺汇率 POST 返回 400 且零写入；用户提交与系统月度汇率不同的显式
汇率但未填写 `rate_reason` 时返回 400；填写理由后保存来源
`manual_line_override`。

- [ ] **Step 5: 运行 Task 6 测试并提交**

```bash
../../.venv/bin/python -m pytest tests/dashboard/test_routes.py tests/dashboard/test_views.py -q
../../.venv/bin/python -m ruff check src/adwatch/dashboard tests/dashboard
git add src/adwatch/dashboard tests/dashboard
git commit -m "feat: close multicurrency accounting web forms"
```

## Task 7: 分润应分、已付、余额和结清状态

**Files:**
- Modify: `src/adwatch/profit_sharing/service.py`
- Modify: `src/adwatch/dashboard/views.py`
- Test: `tests/profit_sharing/test_service.py`
- Test: `tests/dashboard/test_views.py`

- [ ] **Step 1: 写亏损期和余额状态失败测试**

```python
def test_loss_period_allocates_zero_and_reports_loss(sharing):
    service, _ = sharing
    seed_sixty_forty_agreement(service)
    period = service.create_period(starts_on=date(2026, 8, 1), ends_on=date(2026, 8, 31), net_profit_cny=Decimal("-100"), actor="yl")
    detail = service.get_period_detail(period)
    assert {item["partner"]: item["amount_cny"] for item in detail["allocations"]} == {"洁云": Decimal("0.00"), "苏姐": Decimal("0.00")}
    assert detail["display_status"] == "loss"


def test_partial_and_full_payments_update_partner_balances(sharing):
    service, _ = sharing
    seed_sixty_forty_agreement(service)
    period = create_confirmed_period(service, Decimal("1000"))
    service.record_payment(period_id=period, partner="洁云", amount_cny=Decimal("300"), paid_on=date(2026, 9, 1), status="paid", note="首笔", actor="yl")
    detail = service.get_period_detail(period)
    assert allocation(detail, "洁云") == {"due_cny": Decimal("600.00"), "paid_cny": Decimal("300.00"), "outstanding_cny": Decimal("300.00")}
    assert detail["display_status"] == "paying"
    pay_all_outstanding(service, period)
    assert service.get_period_detail(period)["display_status"] == "settled"
```

- [ ] **Step 2: 修正可分配利润**

在 `create_period()` 中：

```python
distributable = max(_money(net_profit_cny), Decimal("0.00"))
allocation = _money(distributable * ratio)
```

净利润字段仍保存真实负数；仅 `profit_allocations.amount_cny` 使用非负可分配利润。

- [ ] **Step 3: 实现 `get_period_detail()` 和列表详情**

单次查询聚合 `profit_allocations` 和有效 `profit_payments`（paid 正数、reversed
负数），返回每位合伙人 `share_ratio`、`due_cny`、`paid_cny`、
`outstanding_cny=max(due-paid,0)`。展示状态：reversed 优先；负/零利润为 loss；
draft；确认且全未付为 ready；部分已付为 paying；全部余额为 0 时 settled。

- [ ] **Step 4: 扩展分润页面**

期间表格展示批次日期、净利润、可分配利润和状态；每期下方逐人显示比例、应分、
已支付、未支付。累计总览按伙伴求和。不得用全局“已支付总额”代替逐期逐人余额。

- [ ] **Step 5: 运行 Task 7 测试并提交**

```bash
../../.venv/bin/python -m pytest tests/profit_sharing/test_service.py tests/dashboard/test_views.py -q
../../.venv/bin/python -m ruff check src/adwatch/profit_sharing src/adwatch/dashboard/views.py tests/profit_sharing tests/dashboard/test_views.py
git add src/adwatch/profit_sharing src/adwatch/dashboard/views.py tests/profit_sharing tests/dashboard/test_views.py
git commit -m "feat: calculate profit sharing balances and settlement state"
```

## Task 8: Excel 历史数据只读预览与重复检测

**Files:**
- Create: `src/adwatch/accounting/excel_preview.py`
- Modify: `src/adwatch/cli.py`
- Test: `tests/accounting/test_excel_preview.py`
- Test: `tests/test_business_cli.py`

- [ ] **Step 1: 写最小工作簿预览失败测试**

```python
def test_excel_preview_classifies_valid_duplicate_conflict_and_missing_rate(tmp_path):
    workbook = build_prototype_workbook(tmp_path / "prototype.xlsx")
    database = migrated_database(tmp_path)
    seed_duplicate_income(database, platform="shopee", order_id="O-1", amount=Decimal("100"))
    result = ExcelPrototypePreview(database).preview(workbook)
    assert result.sheet_names == EXPECTED_ELEVEN_SHEETS
    assert result.counts == {"valid": 2, "duplicate": 1, "conflict": 1, "missing_rate": 1, "invalid": 1}
    assert not database_changed(database)
```

- [ ] **Step 2: 实现只读解析契约**

```python
@dataclass(frozen=True)
class PreviewRow:
    sheet: str
    row_number: int
    status: str
    natural_key: str
    message: str
    normalized: dict[str, str]


@dataclass(frozen=True)
class ExcelPreviewResult:
    sheet_names: tuple[str, ...]
    rows: tuple[PreviewRow, ...]
    counts: dict[str, int]
```

使用 `openpyxl.load_workbook(path, data_only=False, read_only=True)`；检查 11 张表的
中文表头，读取输入单元格而不是公式结果。重复键严格按批准设计；预览过程只能
打开数据库读连接，禁止 `transaction()`、INSERT、UPDATE、DELETE。

- [ ] **Step 3: 写 CLI 失败测试**

```python
def test_business_preview_excel_prints_json_without_importing(tmp_path, monkeypatch, capsys):
    workbook = build_prototype_workbook(tmp_path / "prototype.xlsx")
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    assert main(["business", "preview-accounting-excel", "--file", str(workbook), "--format", "json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["counts"]["valid"] >= 1
    assert not list(Database(settings.database_path).connect().execute("SELECT * FROM manual_income_entries"))
```

- [ ] **Step 4: 注册只读 CLI**

新增：

```text
adwatch business preview-accounting-excel --file PATH --format {text,json}
```

命令输出每表计数及冲突行号；不增加 `import-accounting-excel`，避免用户误以为本轮
已获历史数据写入授权。

- [ ] **Step 5: 运行 Task 8 测试并提交**

```bash
../../.venv/bin/python -m pytest tests/accounting/test_excel_preview.py tests/test_business_cli.py -q
../../.venv/bin/python -m ruff check src/adwatch/accounting/excel_preview.py src/adwatch/cli.py tests/accounting/test_excel_preview.py tests/test_business_cli.py
git add src/adwatch/accounting/excel_preview.py src/adwatch/cli.py tests/accounting/test_excel_preview.py tests/test_business_cli.py
git commit -m "feat: preview and deduplicate accounting workbooks"
```

## Task 9: 文档、全量门禁和公式幻觉检测

**Files:**
- Modify: `README.md`
- Modify: `docs/operations/data-foundation.md`
- Modify: `docs/deploy/README.md`
- Modify: `task_plan.md`
- Modify: `findings.md`
- Modify: `progress.md`

- [ ] **Step 1: 更新用户可见口径**

文档必须明确：

```text
经营净利润 = 结算收入 + 收入调整 - 销售成本 - 平台费
             - 广告实际消耗 - 经营费用 - 刷单成本
```

同时写明采购付款、广告充值、资本投入和提款只影响现金/资产，不进入损益；
月度覆盖只影响新草稿和预览，不静默修改已确认事实；TikTok 自动采集仍关闭。

- [ ] **Step 2: 运行目标和全量测试**

```bash
../../.venv/bin/python -m pytest tests/accounting tests/ledger tests/inventory tests/profit_sharing tests/dashboard tests/reporting tests/storage -q
../../.venv/bin/python -m pytest -q
../../.venv/bin/python -m ruff check src tests
../../.venv/bin/python -m compileall -q src tests
git diff --check
```

Expected: 0 failed；不得只报告目标测试。

- [ ] **Step 3: 执行机械公式幻觉检测**

Run:

```bash
rg -n "purchase.*net_profit|ad_funding.*net_profit|credited_amount.*net_profit" src/adwatch
rg -n "COALESCE\([^)]*refund|COALESCE\([^)]*cost|missing.*0" src/adwatch/accounting src/adwatch/profit_sharing
rg -n "FIXME|NotImplementedError|raise +NotImplemented|pass$" src tests
```

Expected: 第一组不出现把采购/充值扣利润的代码；第二组不出现把缺失经营输入默认
为 0 的代码；第三组无占位实现。逐项复核 11 张 Excel 表均映射到测试或明确排除项。

- [ ] **Step 4: 用确定性样本交叉核算**

运行 `tests/accounting/test_operating_statement.py` 中同一数据集，通过三条独立断言：

```text
明细逐项手算净利润 = 550.00
OperatingStatementService.monthly = 550.00
ProfitSharingService period net profit = 550.00
60/40 应分 = 330.00 / 220.00
```

并断言月报读模型也为 550.00。任何一处不同都视为口径漂移。

- [ ] **Step 5: 代码审查**

使用 `requesting-code-review` 技能，审查重点：迁移向后兼容、币种/汇率冻结、
重复键、状态过滤、采购和充值不入损益、负利润不生成负应分、页面所有写路由
CSRF、Excel 预览零写入、`.env` 排除。修复所有高/中优先级问题后重跑全量门禁。

- [ ] **Step 6: 提交文档和最终修复**

```bash
git add README.md docs/operations/data-foundation.md docs/deploy/README.md task_plan.md findings.md progress.md
git add src tests
git diff --cached --check
git commit -m "docs: document formula parity accounting workflow"
```

## Task 10: 安全部署与生产验收

**Files:**
- Verify: `.dockerignore`
- Verify: `Dockerfile`
- Verify: `docker-compose.yml`
- Create outside repo: 临时安全源码包
- Remote target: `root@43.156.242.184`

- [ ] **Step 1: 发布前本地最终门禁**

```bash
../../.venv/bin/python -m pytest -q
../../.venv/bin/python -m ruff check src tests
../../.venv/bin/python -m compileall -q src tests
git diff --check
git status --short
```

Expected: 全部通过；分支只包含计划内提交。

- [ ] **Step 2: 构建不含敏感数据的源码包**

从 `git archive` 构建已提交文件包，不从工作目录递归打包：

```bash
archive_path="$(mktemp -d)/adwatch-excel-formula-parity.tar.gz"
git archive --format=tar.gz --output="$archive_path" HEAD
tar -tzf "$archive_path" | rg '(^|/)(\.env|.*\.sqlite3?|logs?|evidence|tmp|outputs|Shopee_tiktok泰国店记账\.xlsx)(/|$)' && exit 1 || true
```

Expected: 敏感路径扫描无输出。不得把本地 `.env`、数据库、日志、凭证或原型 Excel
上传服务器。

- [ ] **Step 3: 只读检查远端并备份**

```bash
ssh root@43.156.242.184 'docker ps --filter name=adwatch --format "{{.Names}} {{.Status}}"'
ssh root@43.156.242.184 'cd /opt/adwatch && docker compose exec -T adwatch python -c "import sqlite3; c=sqlite3.connect(\"/app/var/adwatch.sqlite3\"); print(c.execute(\"pragma integrity_check\").fetchone()[0])"'
```

Expected: 容器存在且当前数据库 `integrity_check=ok`。在 `/opt/adwatch/backups/` 创建带时间戳的源码和 SQLite 备份；不得覆盖唯一备份。

- [ ] **Step 4: 上传、解包并重建容器**

使用用户已授权的 SSH 目标上传安全包，解包到版本化发布目录；保留服务器现有
`.env` 和 `var/`，通过 Compose 运行时挂载。执行：

```bash
docker compose build adwatch
docker compose up -d adwatch
docker compose ps
```

Expected: 新容器 healthy；构建上下文不包含 `.env`。

- [ ] **Step 5: 生产只读验收**

检查：

```text
/adwatch/                        HTTP 200，本月/年度经营卡存在
/adwatch/operating-statement     HTTP 200，12 个月表存在
/adwatch/ad-funds                HTTP 200，收入/广告双金额字段存在
/adwatch/inventory               HTTP 200，采购多币种和出库物流存在
/adwatch/operations              HTTP 200，月度汇率表存在
/adwatch/profit-sharing          HTTP 200，应分/已付/未付状态存在
```

数据库验证最新迁移至少 41、`PRAGMA integrity_check=ok`。选一个已有月份只读运行
统一经营汇总，确认采购付款和广告充值未进入净利润。不得在验收过程中导入 Excel
历史数据或修改真实业务金额。

- [ ] **Step 6: 记录部署证据并完成计划**

在 `progress.md` 记录提交、测试计数、镜像 ID、迁移版本、HTTP 状态、数据库完整性、
选定月份的公式交叉核算及仍待用户确认的 Excel 历史导入批次。更新 `task_plan.md`
的 E5/E6；只有全部证据齐全才标记完成。

## 最终规格覆盖检查

| 设计要求 | 实施任务 |
| --- | --- |
| 原币、币种、汇率、CNY 留痕 | Task 1–3 |
| 月度汇率维护与自动解析 | Task 1、6 |
| 收入明细 | Task 2、6 |
| 采购公式、物流和到货 | Task 3 |
| 出库成本与物流展示 | Task 3 |
| 费用多币种 | Task 1、6 |
| 广告支付/实际到账双金额 | Task 2、6 |
| 刷单货款 + 服务费换算 | Task 2、6 |
| 前期投入多币种与出资人 | Task 2、6 |
| 12 月与年度经营汇总 | Task 4、5 |
| 采购/充值不重复扣利润 | Task 4、9 |
| 60/40、已付、余额、结清 | Task 7 |
| 亏损不产生负分润 | Task 7 |
| Excel 历史预览与查重、零写入 | Task 8 |
| TikTok/Live 保持关闭 | Task 0、9、10 |
| 全量测试、审查、幻觉检测、部署 | Task 9、10 |

## 执行纪律

- 每个 Task 严格 RED → GREEN → REFACTOR → 目标测试 → 提交；
- 任何旧测试失败先判断是契约更新还是回归，不为通过测试删除正确业务门禁；
- 已确认事实通过冲销或新版本更正，不直接 UPDATE 金额；
- 不将缺失汇率、成本或退款自动当作 0；
- 不把本地 `.env`、生产数据库或 Excel 原型加入 Git；
- 每批完成后运行相关回归，Task 9/10 运行全量门禁。
