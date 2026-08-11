# Income Statement Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 自动扫描紫鸟下载目录中的平台 Income PDF，按平台、店铺和结算周期保存可审计费率，并在每日利润分析和飞书日报中使用正确版本。

**Architecture:** 新增独立 `income` 领域，PDF 解析、文件发现、持久化和费率解析各自隔离。分析查询按“精确覆盖期→此前最近一期→旧版 SKU 费率”选择费率；每日任务在广告采集前执行扫描，解析失败只产生能力告警，不阻塞广告采集和飞书报告。

**Tech Stack:** Python 3.11+、SQLite、pypdf、pytest、现有 Adwatch CLI、macOS launchd。

---

## 文件结构

- Create: `src/adwatch/income/models.py` — Income 报表值对象和导入结果。
- Create: `src/adwatch/income/shopee_pdf.py` — Shopee PDF 文本提取与字段解析。
- Create: `src/adwatch/income/repository.py` — 报表版本、文件哈希和费率解析查询。
- Create: `src/adwatch/income/service.py` — 单文件导入、目录扫描和店铺映射。
- Create: `src/adwatch/income/__init__.py` — 公开领域接口。
- Modify: `src/adwatch/storage/migrations.py` — 新增报表、导入审计和隔离表。
- Modify: `src/adwatch/storage/analytics.py` — 为每条广告指标选择平台/店铺费率。
- Modify: `src/adwatch/analytics/service.py` — 使用解析后的费率并暴露来源。
- Modify: `src/adwatch/reporting/read_model.py` — 日报读取费率版本。
- Modify: `src/adwatch/reporting/markdown.py` — 飞书日报显示费率和周期。
- Modify: `src/adwatch/cli.py` — `income import|scan|list` 及每日流程接入。
- Modify: `src/adwatch/config.py` — Income 扫描根目录配置。
- Modify: `.env.example` — 扫描目录示例。
- Modify: `pyproject.toml` — 增加 `pypdf` 运行依赖。
- Modify: `README.md` — 使用说明和费率口径。
- Create: `tests/income/test_shopee_pdf.py`
- Create: `tests/income/test_repository.py`
- Create: `tests/income/test_service.py`
- Create: `tests/test_income_cli.py`
- Modify: `tests/analytics/test_service.py`
- Modify: `tests/reporting/test_markdown.py`
- Modify: `tests/test_daily_run_cli.py`
- Modify: `tests/test_config.py`

### Task 1：Income 数据模型和 SQLite 迁移

**Files:**
- Create: `src/adwatch/income/models.py`
- Create: `src/adwatch/income/repository.py`
- Create: `src/adwatch/income/__init__.py`
- Modify: `src/adwatch/storage/migrations.py`
- Create: `tests/income/test_repository.py`
- Modify: `tests/storage/test_analytics_migration.py`

- [ ] **Step 1: 写迁移和版本选择失败测试**

```python
from datetime import date
from decimal import Decimal

from adwatch.income.models import FeeStatement
from adwatch.income.repository import FeeStatementRepository


def test_fee_rate_prefers_covering_period_then_carries_forward(database):
    repository = FeeStatementRepository(database)
    repository.save(
        FeeStatement(
            platform="shopee",
            store="shop",
            period_start=date(2026, 7, 20),
            period_end=date(2026, 7, 26),
            currency="THB",
            product_price=Decimal("4473"),
            commission_fee=Decimal("500"),
            service_fee=Decimal("379"),
            transaction_fee=Decimal("150"),
            affiliate_fee=Decimal("32"),
            sales_fee_total=Decimal("1061"),
            effective_rate=Decimal("0.237201"),
            seller_voucher=Decimal("35"),
            shipping_paid_by_buyer=Decimal("147"),
            shipping_provider_charge=Decimal("965"),
            shipping_rebate=Decimal("818"),
            ads_credit_topup=Decimal("13"),
            adjustment_amount=Decimal("0"),
            total_payout=Decimal("3364"),
            source_path="weekly_report.pdf",
            source_sha256="a" * 64,
        )
    )

    covering = repository.resolve("shopee", "shop", date(2026, 7, 22))
    carried = repository.resolve("shopee", "shop", date(2026, 7, 29))
    other_platform = repository.resolve("tiktok", "shop", date(2026, 7, 22))

    assert covering.rate == Decimal("0.237201")
    assert covering.selection == "covering_period"
    assert carried.rate == Decimal("0.237201")
    assert carried.selection == "carried_forward"
    assert other_platform is None
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest \
  tests/income/test_repository.py \
  tests/storage/test_analytics_migration.py -q
```

Expected: FAIL，提示 `adwatch.income` 不存在。

- [ ] **Step 3: 定义不可变领域模型**

在 `models.py` 创建：

```python
@dataclass(frozen=True)
class FeeStatement:
    platform: str
    store: str
    period_start: date
    period_end: date
    currency: str
    product_price: Decimal
    commission_fee: Decimal
    service_fee: Decimal
    transaction_fee: Decimal
    affiliate_fee: Decimal
    sales_fee_total: Decimal
    effective_rate: Decimal
    seller_voucher: Decimal
    shipping_paid_by_buyer: Decimal
    shipping_provider_charge: Decimal
    shipping_rebate: Decimal
    ads_credit_topup: Decimal
    adjustment_amount: Decimal
    total_payout: Decimal
    source_path: str
    source_sha256: str


@dataclass(frozen=True)
class ResolvedFeeRate:
    rate: Decimal
    period_start: date
    period_end: date
    selection: str
    source_path: str
```

`FeeStatement.__post_init__` 校验平台非空、周期顺序、`product_price > 0`、费用非负、`effective_rate` 在 `0..1`。

- [ ] **Step 4: 新增迁移 18**

新增：

```sql
CREATE TABLE platform_fee_statements (
    platform TEXT NOT NULL,
    store TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    currency TEXT NOT NULL,
    product_price TEXT NOT NULL,
    commission_fee TEXT NOT NULL,
    service_fee TEXT NOT NULL,
    transaction_fee TEXT NOT NULL,
    affiliate_fee TEXT NOT NULL,
    sales_fee_total TEXT NOT NULL,
    effective_rate TEXT NOT NULL,
    seller_voucher TEXT NOT NULL,
    shipping_paid_by_buyer TEXT NOT NULL,
    shipping_provider_charge TEXT NOT NULL,
    shipping_rebate TEXT NOT NULL,
    ads_credit_topup TEXT NOT NULL,
    adjustment_amount TEXT NOT NULL,
    total_payout TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY(platform, store, period_start, period_end)
);
CREATE UNIQUE INDEX platform_fee_source_sha_idx
ON platform_fee_statements(source_sha256);

CREATE TABLE income_import_events (
    id INTEGER PRIMARY KEY,
    source_sha256 TEXT NOT NULL,
    source_path TEXT NOT NULL,
    platform TEXT NOT NULL,
    store TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL
);
```

金额和费率继续使用 `TEXT` 保存，唯一键为
`(platform, store, period_start, period_end)`。

- [ ] **Step 5: 实现 Repository**

`save()` 使用周期唯一键 upsert；若周期相同但 SHA 不同，先写
`income_import_events.status='revised'`。`resolve()` 先查询覆盖日期的周期，
否则查询 `period_end < data_date` 的最近一期。所有查询必须同时匹配
`platform` 和 `store`。

- [ ] **Step 6: 运行测试并提交**

Run:

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest \
  tests/income/test_repository.py \
  tests/storage/test_analytics_migration.py -q
```

Expected: PASS。

Commit:

```bash
git add src/adwatch/income src/adwatch/storage/migrations.py \
  tests/income/test_repository.py tests/storage/test_analytics_migration.py
git commit -m "feat: store versioned platform fee statements"
```

### Task 2：Shopee Income PDF 解析器

**Files:**
- Modify: `pyproject.toml`
- Create: `src/adwatch/income/shopee_pdf.py`
- Create: `tests/income/test_shopee_pdf.py`

- [ ] **Step 1: 写真实字段样本的失败测试**

测试常量使用已脱敏的文本：

```python
SHOPEE_TEXT = """
Statement for 2026-07-20 to 2026-07-26
Username : no4kud44da
Product Price 4,473
Voucher Sponsored by Seller -35
Shipping Fee Paid by Buyer 147
Shipping Fee Charged by Logistic Provider -965
Shipping Rebate From Shopee 818
Commission fee -500
Service Fee -379
Transaction Fee -150
AMS Commission Fee -32
Ads Credit Top-Up (Escrow) -13
Total Payout Released ฿3,364
No payout history within this week.
"""


def test_parse_shopee_statement_excludes_ads_from_sales_rate():
    result = parse_shopee_text(SHOPEE_TEXT, source_path="statement.pdf")
    assert result.store == "no4kud44da"
    assert result.period_start == date(2026, 7, 20)
    assert result.period_end == date(2026, 7, 26)
    assert result.sales_fee_total == Decimal("1061")
    assert result.effective_rate == Decimal("0.237201")
    assert result.ads_credit_topup == Decimal("13")
```

另写缺少 Product Price、Product Price 为零、字段合计不符测试。

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest \
  tests/income/test_shopee_pdf.py -q
```

Expected: FAIL，提示解析函数不存在。

- [ ] **Step 3: 增加 pypdf 依赖并实现解析**

`pyproject.toml` 增加：

```toml
"pypdf>=5,<7",
```

实现：

```python
def extract_pdf_text(path: Path) -> str:
    reader = PdfReader(path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def parse_shopee_text(
    text: str, *, source_path: str, source_sha256: str = ""
) -> FeeStatement:
    period = re.search(
        r"Statement for (\\d{4}-\\d{2}-\\d{2}) "
        r"to (\\d{4}-\\d{2}-\\d{2})",
        text,
    )
    username = re.search(r"Username\\s*:\\s*([^\\s]+)", text)
    if not period or not username:
        raise IncomeParseError("statement period or username is missing")

    def amount(label: str) -> Decimal:
        match = re.search(
            rf"{re.escape(label)}\\s+[-−]?฿?([\\d,]+(?:\\.\\d+)?)",
            text,
            re.IGNORECASE,
        )
        if not match:
            raise IncomeParseError(f"missing field: {label}")
        return Decimal(match.group(1).replace(",", ""))

    product_price = amount("Product Price")
    if product_price <= 0:
        raise IncomeParseError("product price must be positive")
    commission = amount("Commission fee")
    service = amount("Service Fee")
    transaction = amount("Transaction Fee")
    affiliate = amount("AMS Commission Fee")
    sales_total = commission + service + transaction + affiliate
    return FeeStatement(
        platform="shopee",
        store=username.group(1),
        period_start=date.fromisoformat(period.group(1)),
        period_end=date.fromisoformat(period.group(2)),
        currency="THB",
        product_price=product_price,
        commission_fee=commission,
        service_fee=service,
        transaction_fee=transaction,
        affiliate_fee=affiliate,
        sales_fee_total=sales_total,
        effective_rate=(sales_total / product_price).quantize(
            Decimal("0.000001")
        ),
        seller_voucher=amount("Voucher Sponsored by Seller"),
        shipping_paid_by_buyer=amount("Shipping Fee Paid by Buyer"),
        shipping_provider_charge=amount(
            "Shipping Fee Charged by Logistic Provider"
        ),
        shipping_rebate=amount("Shipping Rebate From Shopee"),
        ads_credit_topup=amount("Ads Credit Top-Up (Escrow)"),
        adjustment_amount=Decimal("0"),
        total_payout=amount("Total Payout Released"),
        source_path=source_path,
        source_sha256=source_sha256,
    )


def parse_shopee_pdf(path: Path) -> FeeStatement:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return parse_shopee_text(
        extract_pdf_text(path),
        source_path=str(path),
        source_sha256=digest,
    )
```

金额解析统一去除逗号、货币符号、Unicode 负号，并保存绝对成本。费率使用
`Decimal("0.000001")` 六位精度。

- [ ] **Step 4: 验证解析器**

Run:

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest \
  tests/income/test_shopee_pdf.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml src/adwatch/income/shopee_pdf.py \
  tests/income/test_shopee_pdf.py
git commit -m "feat: parse Shopee income statement PDFs"
```

### Task 3：单文件导入、目录扫描和幂等审计

**Files:**
- Create: `src/adwatch/income/service.py`
- Create: `tests/income/test_service.py`
- Modify: `src/adwatch/config.py`
- Modify: `.env.example`
- Modify: `tests/test_config.py`

- [ ] **Step 1: 写扫描和幂等失败测试**

```python
def test_scan_imports_each_pdf_once_and_quarantines_unknown_store(
    database, tmp_path
):
    known = tmp_path / "虾皮泰国" / "weekly_report_20260720.pdf"
    unknown = tmp_path / "未知店铺" / "weekly_report_20260727.pdf"
    known.parent.mkdir()
    unknown.parent.mkdir()
    known.write_bytes(b"known")
    unknown.write_bytes(b"unknown")

    service = IncomeImportService(
        database,
        scan_root=tmp_path,
        parsers={"shopee": fake_parser},
    )
    first = service.scan()
    second = service.scan()

    assert first.imported == 1
    assert first.quarantined == 1
    assert second.skipped == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest \
  tests/income/test_service.py tests/test_config.py -q
```

Expected: FAIL，提示 `IncomeImportService` 不存在。

- [ ] **Step 3: 实现服务边界**

实现：

```python
@dataclass(frozen=True)
class ScanResult:
    discovered: int = 0
    imported: int = 0
    revised: int = 0
    skipped: int = 0
    quarantined: int = 0


class IncomeImportService:
    def import_file(self, path: Path, *, platform: str) -> ImportResult:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if self.repository.has_source(digest):
            return ImportResult(status="skipped", statement=None)
        parser = self.parsers.get(platform)
        if parser is None:
            raise IncomeImportError(f"unsupported platform: {platform}")
        statement = parser(path)
        status = self.repository.save(statement)
        return ImportResult(status=status, statement=statement)

    def scan(self) -> ScanResult:
        result = ScanResult()
        for path in sorted(self.scan_root.rglob("weekly_report_*.pdf")):
            result = result.add(self._scan_one(path))
        return result
```

扫描仅使用 `rglob("weekly_report_*.pdf")`；先算 SHA，再检查导入事件。Shopee
由 PDF Username 和 `store_aliases` 解析；无法唯一映射时写
`status='quarantined'`。隔离不复制 PDF，不输出个人字段。

- [ ] **Step 4: 配置扫描根目录**

`Settings` 增加：

```python
income_scan_root: Path = Path(
    "/Users/yl/Library/Application Support/ziniaobrowserdatas"
)
```

环境变量名：

```text
ADWATCH_INCOME_SCAN_ROOT
```

`.env.example` 使用空值并注释默认 macOS 路径。

- [ ] **Step 5: 运行测试并提交**

Run:

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest \
  tests/income/test_service.py tests/test_config.py -q
```

Expected: PASS。

Commit:

```bash
git add src/adwatch/income/service.py src/adwatch/config.py \
  .env.example tests/income/test_service.py tests/test_config.py
git commit -m "feat: scan and audit income statement imports"
```

### Task 4：利润分析按平台、店铺和日期选择费率

**Files:**
- Modify: `src/adwatch/storage/analytics.py`
- Modify: `src/adwatch/analytics/service.py`
- Modify: `tests/analytics/test_service.py`

- [ ] **Step 1: 写跨平台隔离和沿用失败测试**

```python
def test_analysis_uses_store_fee_statement_without_cross_platform_leak(
    database,
):
    seed_metric(database, platform="shopee", store="shop", date="2026-07-29")
    seed_metric(database, platform="tiktok", store="shop", date="2026-07-29")
    seed_statement(
        database,
        platform="shopee",
        store="shop",
        period_end="2026-07-26",
        rate="0.237201",
    )

    summary = AnalysisService(database).run(date(2026, 7, 29))

    assert profit_rate(database, platform="shopee") == Decimal("0.237201")
    assert has_missing_commission_alert(database, platform="tiktok")
    assert summary.profit_results == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest \
  tests/analytics/test_service.py -q
```

Expected: FAIL，Shopee 仍只读取 `product_costs.commission_rate`。

- [ ] **Step 3: 在分析查询中增加费率解析**

为每条 metric 增加相关子查询：

```sql
COALESCE(
  (
    SELECT statement.effective_rate
    FROM platform_fee_statements statement
    WHERE statement.platform=metric.platform
      AND statement.store=metric.store
      AND statement.period_start<=metric.data_date
    ORDER BY
      CASE WHEN statement.period_end>=metric.data_date THEN 0 ELSE 1 END,
      statement.period_end DESC
    LIMIT 1
  ),
  cost.commission_rate
) AS resolved_commission_rate
```

同时返回 `fee_period_start`、`fee_period_end`、`fee_source_path` 和
`fee_selection`。不得修改旧表原值。

- [ ] **Step 4: 分析服务使用解析字段**

必填字段从 `commission_rate` 改为 `resolved_commission_rate`，
`ProfitInput.commission_rate` 使用解析值。利润结果无需复制费率；来源通过日报
读取模型查询。

- [ ] **Step 5: 验证并提交**

Run:

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest \
  tests/analytics tests/storage -q
```

Expected: PASS。

Commit:

```bash
git add src/adwatch/storage/analytics.py src/adwatch/analytics/service.py \
  tests/analytics/test_service.py
git commit -m "feat: resolve platform fee rates by store and date"
```

### Task 5：Income CLI 和每日 10:00 工作流接入

**Files:**
- Modify: `src/adwatch/cli.py`
- Create: `tests/test_income_cli.py`
- Modify: `tests/test_daily_run_cli.py`

- [ ] **Step 1: 写 CLI 和执行顺序失败测试**

```python
def test_income_cli_import_scan_and_list(tmp_path, monkeypatch, capsys):
    assert main(["income", "import", "--file", str(pdf), "--platform", "shopee"]) == 0
    assert main(["income", "scan"]) == 0
    assert main(["income", "list"]) == 0
    assert "23.7201%" in capsys.readouterr().out


def test_daily_run_scans_income_before_analysis(monkeypatch):
    calls = []
    monkeypatch.setattr(
        IncomeImportService, "scan", lambda self: calls.append("income")
    )
    monkeypatch.setattr(
        AnalysisService, "run", lambda self, day: calls.append("analysis")
    )
    main(["run", "daily", "--mode", "mock", "--date", "2026-07-29"])
    assert calls.index("income") < calls.index("analysis")
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest \
  tests/test_income_cli.py tests/test_daily_run_cli.py -q
```

Expected: FAIL，`income` 子命令不存在。

- [ ] **Step 3: 实现三个子命令**

Parser：

```text
income import --file PATH --platform {shopee,tiktok}
income scan
income list
```

`list` 输出：

```text
platform store period rate selection source
```

CLI 错误返回 2，错误消息不得包含 PDF 中的姓名、地址或银行账号。

- [ ] **Step 4: 接入 daily**

`run daily` 在 collector 循环之前调用 `IncomeImportService.scan()`。
扫描异常转换为：

```text
income_scan=partial imported=N quarantined=N error=<type>
```

异常不得跳过广告采集、分析、报告或飞书发送。

- [ ] **Step 5: 验证并提交**

Run:

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest \
  tests/test_income_cli.py tests/test_daily_run_cli.py tests/test_cli.py -q
```

Expected: PASS。

Commit:

```bash
git add src/adwatch/cli.py tests/test_income_cli.py \
  tests/test_daily_run_cli.py
git commit -m "feat: import income statements in daily workflow"
```

### Task 6：日报和飞书展示费率版本

**Files:**
- Modify: `src/adwatch/reporting/read_model.py`
- Modify: `src/adwatch/reporting/markdown.py`
- Modify: `tests/reporting/test_read_model.py`
- Modify: `tests/reporting/test_markdown.py`

- [ ] **Step 1: 写日报来源失败测试**

```python
def test_daily_report_shows_effective_fee_rate_and_source(database):
    snapshot = ReportReadModel(database).daily(date(2026, 7, 29))
    report = render_daily_markdown(snapshot, simulated=False)

    assert "Shopee 综合销售费率：23.7201%" in report
    assert "周期：2026-07-20 至 2026-07-26（沿用）" in report
    assert "来源：weekly_report_20260720.pdf" in report
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest \
  tests/reporting/test_read_model.py tests/reporting/test_markdown.py -q
```

Expected: FAIL，`DailySnapshot` 没有费率版本。

- [ ] **Step 3: 扩展读取模型**

新增：

```python
@dataclass(frozen=True)
class FeeRateSummary:
    platform: str
    store: str
    rate: Decimal
    period_start: date
    period_end: date
    selection: str
    source_name: str
```

`DailySnapshot` 增加 `fee_rates: tuple[FeeRateSummary, ...]`。查询复用
Repository 的选择规则，不复制另一套日期逻辑。

- [ ] **Step 4: 渲染飞书内容**

在“经营分析可信度”之前新增“平台实际费率”：

```text
- Shopee/no4kud44da 综合销售费率：23.7201%
  周期：2026-07-20 至 2026-07-26（沿用）
  来源：weekly_report_20260720.pdf
```

只输出 `Path(source_path).name`，禁止输出完整用户目录。

- [ ] **Step 5: 验证并提交**

Run:

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest tests/reporting -q
```

Expected: PASS。

Commit:

```bash
git add src/adwatch/reporting/read_model.py \
  src/adwatch/reporting/markdown.py tests/reporting
git commit -m "feat: show fee statement lineage in daily reports"
```

### Task 7：真实 Shopee PDF 验收、文档和全量验证

**Files:**
- Modify: `README.md`
- Modify: `var/reports/上线待办-2026-07-28.md`（现场数据，不提交）

- [ ] **Step 1: 安装本项目声明依赖**

Run:

```bash
../../.venv/bin/python -m pip install -e .
```

Expected: 安装 `pypdf`，Adwatch 可编辑安装成功。

- [ ] **Step 2: 导入用户真实 PDF**

Run:

```bash
../../.venv/bin/adwatch income import \
  --platform shopee \
  --file "/Users/yl/Library/Application Support/ziniaobrowserdatas/ziniao browser/虾皮泰国/weekly_report_20260720 (1).pdf"
```

Expected:

```text
imported shopee/no4kud44da 2026-07-20..2026-07-26 rate=23.7201%
```

- [ ] **Step 3: 验证覆盖期和沿用**

Run:

```bash
../../.venv/bin/adwatch income list
../../.venv/bin/adwatch analyze --date 2026-07-26
../../.venv/bin/adwatch analyze --date 2026-07-27
```

Expected: 两天均使用 `0.237201`；7 月 26 日标记精确覆盖，7 月 27 日标记沿用；无 `missing_business_input: commission_rate`。

- [ ] **Step 4: 验证重复扫描**

Run:

```bash
../../.venv/bin/adwatch income scan
../../.venv/bin/adwatch income scan
```

Expected: 第二次 `imported=0`，同一 SHA 记录为 skipped，不新增报表版本。

- [ ] **Step 5: 更新 README**

增加：

- 默认扫描目录和 `ADWATCH_INCOME_SCAN_ROOT`；
- 手动 import/scan/list 命令；
- 周期覆盖和最近一期沿用规则；
- 销售费率包含项和 Ads Credit Top-Up 排除规则；
- TikTok 需要真实结算样本后才能激活解析器。

- [ ] **Step 6: 全量测试和静态检查**

Run:

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest -q
../../.venv/bin/python -m ruff check src tests
```

Expected: 全部测试 PASS，Ruff 无错误。

- [ ] **Step 7: 备份现场数据库**

Run:

```bash
../../.venv/bin/adwatch backup create \
  --output var/backups/post-income-import-2026-07-28.sqlite3
../../.venv/bin/adwatch backup verify \
  --path var/backups/post-income-import-2026-07-28.sqlite3
```

Expected: `integrity=ok`。

- [ ] **Step 8: 提交**

```bash
git add README.md
git commit -m "docs: document automated income imports"
```

- [ ] **Step 9: 分支完成检查**

Run:

```bash
git status --short
git log --oneline --decorate -10
```

Expected: 只有明确保留的现场 `var/` 文件，开发分支代码已全部提交。
