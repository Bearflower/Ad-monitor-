# AdWatch P1-A Shopee Commerce Facts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Also use `test-driven-development`, `requesting-code-review`, and `verification-before-completion`.

**Goal:** 在现有 Shopee 商品/订单只读同步上增加配置化 T+3 回溯、退款状态观测、物流观测和平台库存历史快照，为后续结算、利润回算和库存安全门禁提供真实、可追溯的上游事实。

**Architecture:** 保留 `ShopeeOrderSync` 作为单一紫鸟 Shopee 读取编排，复用 `PlatformOrderLine` 与 Item/Model/Seller SKU 映射。退款和物流观测写入现有 `integrations/commerce.py` 的仓储边界，平台库存快照写入 `integrations/inventory.py`；`order_sync_runs` 扩展为本次同步的统一运行记录。退款金额无法从订单卡可靠获得时保存 `NULL` 和 `missing` 语义，禁止按零计算。

**Tech Stack:** Python 3.11+、SQLite WAL、紫鸟 CLI/ZClaw、pytest、Ruff、标准库 `dataclasses/datetime/decimal`。

---

## 0. 实施边界

- 唯一代码源是当前 workspace，不读取或复制旧 Documents 项目。
- 只操作 Shopee；`ADWATCH_TIKTOK_COLLECTION_ENABLED=false` 保持不变。
- 只使用紫鸟内已登录页面的固定只读请求，不点击平台业务操作按钮。
- 不保存 `SPC_CDS`、Cookie、买家姓名、地址、电话或其他 PII。
- 本批不计算最终退款金额、不重算利润；P1-B/P1-C 分别负责金额来源和利润消费。
- 本批不部署、不提交、不推送；只有用户明确要求后才执行 Git 或服务器操作。
- 数据迁移版本使用当前下一版本 `21`；实施前若主分支已出现更高版本，先机械改为新的下一版本，并同步测试中的期望值。

## 1. 文件结构

**Create:**

- `src/adwatch/orders/observations.py`：把已解析订单行和商品 SKU 转换为去重后的平台事实观测。
- `tests/orders/test_observations.py`：退款、物流、库存观测的纯函数测试。
- `tests/storage/test_commerce_fact_migration.py`：迁移 21、唯一键和旧库升级测试。
- `tests/fixtures/shopee/commerce_order_cards.json`：不含 PII 的订单卡契约夹具。

**Modify:**

- `src/adwatch/config.py`：增加 Shopee 历史回溯天数配置。
- `.env.example`：公开配置说明。
- `src/adwatch/integrations/commerce.py`：退款/物流观测模型与幂等仓储。
- `src/adwatch/integrations/inventory.py`：平台库存快照模型与幂等仓储。
- `src/adwatch/orders/models.py`：扩展同步结果所需类型边界时只做最小修改。
- `src/adwatch/orders/repository.py`：扩展统一运行记录的日期范围和计数。
- `src/adwatch/orders/ziniao_sync.py`：持久化三类观测并返回计数。
- `src/adwatch/storage/migrations.py`：新增迁移 21。
- `src/adwatch/operations/daily_run.py`：按配置回看报告日之前 3 天。
- `src/adwatch/cli.py`：`orders sync` 输出新增事实计数。
- `tests/test_config.py`：环境变量与非法值测试。
- `tests/integrations/test_commerce.py`：退款/物流仓储测试。
- `tests/integrations/test_contracts.py`：静态契约与新事实契约兼容测试。
- `tests/orders/test_repository.py`：运行记录扩展测试。
- `tests/orders/test_ziniao_sync.py`：同步顺序、计数、部分失败和幂等测试。
- `tests/operations/test_daily_run.py`：T+3 日期范围、失败隔离和消息测试。
- `tests/test_cli.py`：命令输出契约测试。
- `README.md`：真实能力和边界说明。
- `docs/operations/data-foundation.md`：表、来源和新鲜度说明。
- `findings.md`：完成后更新矩阵证据，不提前改状态。

---

### Task 1: 冻结无 PII 的 Shopee 订单事实契约

**Files:**

- Create: `tests/fixtures/shopee/commerce_order_cards.json`
- Modify: `tests/orders/test_shopee_parser.py`

- [x] **Step 1: 创建最小脱敏夹具**

夹具只保留当前已验证的订单卡字段，至少包含 `Completed`、`Return/Refund` 和取消三种状态。退款金额没有稳定字段，因此不放入猜测值。

```json
[
  {
    "order_card": {
      "card_header": {"order_sn": "260801REFUND01"},
      "status_info": {"status": "Return/Refund"},
      "order_ext_info": {"order_id": 1001, "logistics_status": 5},
      "payment_info": {"total_price": 10000000},
      "item_info_group": {
        "item_info_list": [{
          "item_list": [{
            "name": "Sanitized Product",
            "description": "Variation: Blue",
            "amount": 1,
            "inner_item_ext_info": {"item_id": 123, "model_id": 456}
          }]
        }]
      }
    }
  }
]
```

- [x] **Step 2: 写失败的 PII 与字段契约测试**

```python
import json
from pathlib import Path


FIXTURE = Path("tests/fixtures/shopee/commerce_order_cards.json")


def test_commerce_fixture_contains_only_required_non_pii_fields():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    for forbidden in ("buyer_name", "phone", "address", "spc_cds", "cookie"):
        assert forbidden not in serialized
    card = payload[0]["order_card"]
    assert card["card_header"]["order_sn"] == "260801REFUND01"
    assert card["status_info"]["status"] == "Return/Refund"
    assert card["order_ext_info"]["logistics_status"] == 5
```

- [x] **Step 3: 运行测试并确认 RED**

Run: `.venv/bin/python -m pytest tests/orders/test_shopee_parser.py::test_commerce_fixture_contains_only_required_non_pii_fields -q`

Expected: FAIL，因为夹具尚不存在。

- [x] **Step 4: 保存夹具并确认 GREEN**

Run: `.venv/bin/python -m pytest tests/orders/test_shopee_parser.py -q`

Expected: PASS；夹具中不存在 PII 或会话字段。

---

### Task 2: 增加迁移 21 和统一运行计数

**Files:**

- Modify: `src/adwatch/storage/migrations.py`
- Create: `tests/storage/test_commerce_fact_migration.py`

- [x] **Step 1: 写迁移失败测试**

```python
from adwatch.storage.db import Database


def test_migration_21_creates_commerce_observation_tables(tmp_path):
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()
    with database.connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        run_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(order_sync_runs)")
        }
    assert {
        "platform_refund_observations",
        "platform_logistics_observations",
        "platform_inventory_snapshots",
    } <= names
    assert {
        "start_date", "end_date", "refund_count",
        "logistics_count", "inventory_snapshot_count",
    } <= run_columns
```

- [x] **Step 2: 运行测试并确认 RED**

Run: `.venv/bin/python -m pytest tests/storage/test_commerce_fact_migration.py -q`

Expected: FAIL，三个表和扩展列不存在。

- [x] **Step 3: 添加迁移 21**

```sql
CREATE TABLE platform_refund_observations (
    platform TEXT NOT NULL,
    store TEXT NOT NULL,
    order_id TEXT NOT NULL,
    observed_on TEXT NOT NULL,
    order_date TEXT NOT NULL,
    refund_status TEXT NOT NULL,
    amount_original TEXT,
    currency TEXT NOT NULL,
    source_updated_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY(platform, store, order_id, observed_on),
    CHECK(amount_original IS NULL OR CAST(amount_original AS REAL) >= 0)
);

CREATE TABLE platform_logistics_observations (
    platform TEXT NOT NULL,
    store TEXT NOT NULL,
    order_id TEXT NOT NULL,
    observed_on TEXT NOT NULL,
    order_date TEXT NOT NULL,
    logistics_status TEXT NOT NULL,
    source_updated_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY(platform, store, order_id, observed_on)
);

CREATE TABLE platform_inventory_snapshots (
    platform TEXT NOT NULL,
    store TEXT NOT NULL,
    item_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    seller_sku TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    available_units INTEGER NOT NULL CHECK(available_units >= 0),
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY(platform, store, item_id, model_id, snapshot_date)
);

ALTER TABLE order_sync_runs ADD COLUMN start_date TEXT;
ALTER TABLE order_sync_runs ADD COLUMN end_date TEXT;
ALTER TABLE order_sync_runs ADD COLUMN refund_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE order_sync_runs ADD COLUMN logistics_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE order_sync_runs
    ADD COLUMN inventory_snapshot_count INTEGER NOT NULL DEFAULT 0;
```

- [x] **Step 4: 补旧库升级与唯一键测试**

先应用迁移 1–20，再运行 `Database.migrate()`；断言迁移 21 只应用一次。同一订单同一观测日重复插入必须命中唯一键，不得增加第二行。

- [x] **Step 5: 运行存储测试**

Run: `.venv/bin/python -m pytest tests/storage/test_commerce_fact_migration.py tests/storage/test_migrations.py -q`

Expected: PASS。

---

### Task 3: 实现退款、物流和库存事实仓储

**Files:**

- Modify: `src/adwatch/integrations/commerce.py`
- Modify: `src/adwatch/integrations/inventory.py`
- Modify: `tests/integrations/test_commerce.py`
- Modify: `tests/integrations/test_contracts.py`

- [x] **Step 1: 写幂等与历史观测失败测试**

```python
def test_refund_observation_is_idempotent_per_observed_day(tmp_path):
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()
    repository = CommerceRepository(database)
    item = RefundObservation(
        platform="shopee", store="shop", order_id="ORDER-1",
        observed_on=date(2026, 8, 4), order_date=date(2026, 8, 1),
        refund_status="returned", amount_original=None, currency="THB",
        source_updated_at=datetime(2026, 8, 4, tzinfo=UTC),
        observed_at=datetime(2026, 8, 4, 9, tzinfo=UTC),
        source="shopee_order_card",
    )
    assert repository.upsert_refund_observations((item,)) == 1
    assert repository.upsert_refund_observations((item,)) == 0
```

另写测试：8 月 5 日同一订单状态变为 `refund_completed` 时新增第二个观测日，8 月 4 日记录仍存在；库存同一日期覆盖为最新观测值、跨日期保留历史。

- [x] **Step 2: 运行测试并确认 RED**

Run: `.venv/bin/python -m pytest tests/integrations/test_commerce.py tests/integrations/test_contracts.py -q`

Expected: FAIL，观测类型和仓储方法不存在。

- [x] **Step 3: 添加不可变观测模型**

```python
@dataclass(frozen=True)
class RefundObservation:
    platform: str
    store: str
    order_id: str
    observed_on: date
    order_date: date
    refund_status: str
    amount_original: Decimal | None
    currency: str
    source_updated_at: datetime
    observed_at: datetime
    source: str


@dataclass(frozen=True)
class LogisticsObservation:
    platform: str
    store: str
    order_id: str
    observed_on: date
    order_date: date
    logistics_status: str
    source_updated_at: datetime
    observed_at: datetime
    source: str
```

```python
@dataclass(frozen=True)
class PlatformInventorySnapshot:
    platform: str
    store: str
    item_id: str
    model_id: str
    seller_sku: str
    snapshot_date: date
    available_units: int
    observed_at: datetime
    source: str
```

- [x] **Step 4: 实现幂等仓储**

新增 `CommerceRepository.upsert_refund_observations()`、
`CommerceRepository.upsert_logistics_observations()` 和
`InventoryFactRepository.upsert_snapshots()`。同一日内容完全相同返回 0；同一日
有更新时覆盖当天观测并返回 1；跨日只新增，不改旧日。

- [x] **Step 5: 运行仓储测试**

Run: `.venv/bin/python -m pytest tests/integrations -q`

Expected: PASS。

---

### Task 4: 把订单行和商品 SKU 转换为去重观测

**Files:**

- Create: `src/adwatch/orders/observations.py`
- Create: `tests/orders/test_observations.py`

- [x] **Step 1: 写失败测试**

```python
def order_line(*, order_id: str, model_id: str, refund_status: str):
    return PlatformOrderLine(
        platform="shopee", store="shop", order_id=order_id,
        item_id="item-1", model_id=model_id, seller_sku=f"SKU-{model_id}",
        variation_name="Blue", product_name="Product", quantity=1,
        buyer_paid=Decimal("10"), currency="THB", order_status="completed",
        logistics_status="delivered", refund_status=refund_status,
        ordered_at=date(2026, 8, 1),
        source_updated_at=datetime(2026, 8, 4, tzinfo=UTC),
    )


def test_order_lines_create_one_logistics_and_refund_observation_per_order():
    lines = (
        order_line(order_id="ORDER-1", model_id="1", refund_status="returned"),
        order_line(order_id="ORDER-1", model_id="2", refund_status="returned"),
    )
    refunds, logistics = order_observations(
        lines, observed_at=datetime(2026, 8, 4, 9, tzinfo=UTC)
    )
    assert len(refunds) == 1
    assert refunds[0].amount_original is None
    assert len(logistics) == 1
```

```python
def platform_sku(*, inventory_units: int):
    return PlatformSku(
        platform="shopee", store="shop", item_id="item-1", model_id="model-1",
        seller_sku="SKU-1", variation_name="Blue", product_name="Product",
        inventory_units=inventory_units,
        observed_at=datetime(2026, 8, 4, 9, tzinfo=UTC),
    )


def test_skus_create_daily_inventory_snapshots():
    snapshots = inventory_observations(
        (platform_sku(inventory_units=8),),
        observed_at=datetime(2026, 8, 4, 9, tzinfo=UTC),
    )
    assert snapshots[0].snapshot_date == date(2026, 8, 4)
    assert snapshots[0].available_units == 8
```

- [x] **Step 2: 运行测试并确认 RED**

Run: `.venv/bin/python -m pytest tests/orders/test_observations.py -q`

Expected: FAIL，`order_observations` 和 `inventory_observations` 不存在。

- [x] **Step 3: 实现纯函数**

```python
def order_observations(
    lines: Iterable[PlatformOrderLine], *, observed_at: datetime
) -> tuple[tuple[RefundObservation, ...], tuple[LogisticsObservation, ...]]:
    refunds: dict[tuple[str, str, str], RefundObservation] = {}
    logistics: dict[tuple[str, str, str], LogisticsObservation] = {}
    for line in lines:
        key = (line.platform, line.store, line.order_id)
        if line.logistics_status.strip():
            logistics[key] = LogisticsObservation(
                platform=line.platform,
                store=line.store,
                order_id=line.order_id,
                observed_on=observed_at.date(),
                order_date=line.ordered_at,
                logistics_status=line.logistics_status.strip(),
                source_updated_at=line.source_updated_at,
                observed_at=observed_at,
                source="shopee_order_card",
            )
        if line.refund_status.strip():
            refunds[key] = RefundObservation(
                platform=line.platform,
                store=line.store,
                order_id=line.order_id,
                observed_on=observed_at.date(),
                order_date=line.ordered_at,
                refund_status=line.refund_status.strip(),
                amount_original=None,
                currency=line.currency,
                source_updated_at=line.source_updated_at,
                observed_at=observed_at,
                source="shopee_order_card",
            )
    return tuple(refunds.values()), tuple(logistics.values())


def inventory_observations(
    skus: Iterable[PlatformSku], *, observed_at: datetime
) -> tuple[PlatformInventorySnapshot, ...]:
    return tuple(
        PlatformInventorySnapshot(
            platform=sku.platform,
            store=sku.store,
            item_id=sku.item_id,
            model_id=sku.model_id,
            seller_sku=sku.seller_sku,
            snapshot_date=observed_at.date(),
            available_units=sku.inventory_units,
            observed_at=observed_at,
            source="shopee_product_api",
        )
        for sku in skus
    )
```

实现约束：只有非空 `refund_status` 才生成退款观测；取消订单不自动当成退款；
物流状态为空时不生成物流观测；金额始终为 `None`，直到 P1-B 提供可靠来源。

- [x] **Step 4: 运行纯函数测试**

Run: `.venv/bin/python -m pytest tests/orders/test_observations.py tests/orders/test_shopee_parser.py -q`

Expected: PASS。

---

### Task 5: 扩展 ShopeeOrderSync 的统一持久化与运行记录

**Files:**

- Modify: `src/adwatch/orders/repository.py`
- Modify: `src/adwatch/orders/ziniao_sync.py`
- Modify: `tests/orders/test_repository.py`
- Modify: `tests/orders/test_ziniao_sync.py`

- [x] **Step 1: 写同步计数和幂等失败测试**

```python
def test_sync_persists_commerce_observations_and_counts(tmp_path):
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()
    service = ShopeeOrderSync(
        OrderRepository(database),
        FakeReader(),
        store="shop",
        clock=lambda: datetime(2026, 8, 4, 9, tzinfo=UTC),
    )
    first = service.sync(start=date(2026, 8, 1), end=date(2026, 8, 4))
    second = service.sync(start=date(2026, 8, 1), end=date(2026, 8, 4))
    assert first.logistics_count == 1
    assert first.inventory_snapshot_count == 1
    assert second.logistics_count == 0
    assert second.inventory_snapshot_count == 0
```

退款夹具应使 `refund_count == 1`。运行记录必须保存 `start_date/end_date` 和
三类计数；解析拒绝使状态为 `partial`，数据库错误使状态为 `failed`，旧事实保留。

- [x] **Step 2: 运行测试并确认 RED**

Run: `.venv/bin/python -m pytest tests/orders/test_repository.py tests/orders/test_ziniao_sync.py -q`

Expected: FAIL，结果和运行记录没有新增计数。

- [x] **Step 3: 扩展同步结果**

```python
@dataclass(frozen=True)
class OrderSyncResult:
    status: str
    order_count: int
    sku_count: int
    rejected_count: int
    refund_count: int = 0
    logistics_count: int = 0
    inventory_snapshot_count: int = 0
```

- [x] **Step 4: 在现有读取循环内持久化观测**

商品页解析后先写 SKU 映射，再写当天平台库存快照；订单卡解析后先写订单行，
再从成功解析的订单行生成退款和物流观测。所有观测使用同一个 `started_at` 作为
`observed_at`，运行日期范围来自 `sync(start,end)`。

- [x] **Step 5: 扩展运行记录 API**

`start_sync_run()` 增加 `start/end`；`finish_sync_run()` 增加三类计数。错误摘要
继续限制 500 字符，不包含原始响应或会话参数。

- [x] **Step 6: 运行订单同步测试**

Run: `.venv/bin/python -m pytest tests/orders -q`

Expected: PASS。

---

### Task 6: 配置 T+3 回溯并接入每天 09:00 流程

**Files:**

- Modify: `src/adwatch/config.py`
- Modify: `.env.example`
- Modify: `src/adwatch/operations/daily_run.py`
- Modify: `tests/test_config.py`
- Modify: `tests/operations/test_daily_run.py`
- Modify: `tests/test_daily_run_cli.py`

- [x] **Step 1: 写配置与日期范围失败测试**

```python
def test_shopee_history_lookback_days_defaults_to_three(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADWATCH_SHOPEE_HISTORY_LOOKBACK_DAYS", raising=False)
    assert Settings.from_env().shopee_history_lookback_days == 3
```

```python
from datetime import UTC, date, datetime

from adwatch.config import Settings
from adwatch.operations import daily_run
from adwatch.operations.daily_run import ProductionDailyRunDependencies
from adwatch.orders.ziniao_sync import OrderSyncResult
from adwatch.storage.db import Database


def test_daily_sync_looks_back_three_days(tmp_path, monkeypatch):
    calls = []

    class RecordingSync:
        def __init__(self, repository, reader, *, store):
            assert store == "shop"

        def sync(self, *, start, end):
            calls.append((start, end))
            return OrderSyncResult("success", 0, 0, 0)

    monkeypatch.setattr(daily_run, "ShopeeOrderSync", RecordingSync)
    monkeypatch.setattr(
        daily_run, "ZiniaoShopeeReadClient", lambda client, store_id: object()
    )
    settings = Settings(
        data_dir=tmp_path,
        ziniao_shopee_store_id="222",
        ziniao_shopee_store_name="shop",
        tiktok_collection_enabled=False,
        shopee_history_lookback_days=3,
    )
    database = Database(settings.database_path)
    database.migrate()
    dependencies = ProductionDailyRunDependencies(settings, database)
    dependencies.sync_orders(date(2026, 8, 4), "ziniao")
    assert calls == [(date(2026, 8, 1), date(2026, 8, 4))]
```

- [x] **Step 2: 运行测试并确认 RED**

Run: `.venv/bin/python -m pytest tests/test_config.py tests/operations/test_daily_run.py tests/test_daily_run_cli.py -q`

Expected: FAIL，配置字段和回溯范围不存在。

- [x] **Step 3: 增加集中配置**

```python
shopee_history_lookback_days: int = 3
```

`Settings.from_env()` 使用已有 `positive_int()` 读取：

```text
ADWATCH_SHOPEE_HISTORY_LOOKBACK_DAYS=3
```

非法值 `0`、负数或非整数必须启动失败，不静默回退。

- [x] **Step 4: 调整生产日任务日期范围**

```python
start = report_date - timedelta(days=self.settings.shopee_history_lookback_days)
return service.sync(start=start, end=report_date)
```

日任务顺序保持：汇率 → Bridge → Shopee 商品/订单/经营事实 → 已启用平台广告 →
本地订单处理 → 分析/报告。同步失败继续记录 `order_sync` incident，并允许广告报告降级完成。

- [x] **Step 5: 运行日任务测试**

Run: `.venv/bin/python -m pytest tests/test_config.py tests/operations/test_daily_run.py tests/test_daily_run_cli.py -q`

Expected: PASS。

---

### Task 7: 扩展 CLI 计数和安全输出

**Files:**

- Modify: `src/adwatch/cli.py`
- Modify: `tests/test_cli.py`

- [x] **Step 1: 写 CLI 输出失败测试**

```python
from adwatch.config import Settings
from adwatch.orders.ziniao_sync import OrderSyncResult


def test_orders_sync_reports_commerce_fact_counts(tmp_path, monkeypatch, capsys):
    settings = Settings(
        data_dir=tmp_path,
        ziniao_shopee_store_id="222",
        ziniao_shopee_store_name="shop",
        tiktok_collection_enabled=False,
    )
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)

    class FakeSync:
        def __init__(self, repository, reader, *, store):
            assert store == "shop"

        def sync(self, *, start, end):
            return OrderSyncResult("success", 7, 281, 0, 1, 7, 281)

    monkeypatch.setattr(adwatch.cli, "ShopeeOrderSync", FakeSync)
    monkeypatch.setattr(
        adwatch.cli,
        "ZiniaoShopeeReadClient",
        lambda client, store_id: object(),
    )
    assert adwatch.cli.main([
        "orders", "sync", "--platform", "shopee",
        "--from", "2026-08-01", "--to", "2026-08-04",
    ]) == 0
    output = capsys.readouterr().out
    assert "refunds=1" in output
    assert "logistics=7" in output
    assert "inventory_snapshots=281" in output
```

- [x] **Step 2: 运行测试并确认 RED**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`

Expected: FAIL，输出没有新增计数。

- [x] **Step 3: 输出固定摘要**

输出只包含状态和计数：

```text
order_sync=success orders=7 skus=281 rejected=0 refunds=1 logistics=7 inventory_snapshots=281
```

不得输出页面 URL 查询参数、原始响应、商品买家信息或 Cookie。

- [x] **Step 4: 运行 CLI 测试**

Run: `.venv/bin/python -m pytest tests/test_cli.py tests/test_cli_end_to_end.py -q`

Expected: PASS。

---

### Task 8: 真实 Shopee 只读回溯与幂等验收

**Files:**

- Modify: `progress.md`
- Modify: `findings.md`（仅在验收通过后）

- [x] **Step 1: 执行现场前置检查**

Run: `.venv/bin/adwatch doctor`

Expected: 紫鸟 CLI、Bridge、Shopee 店铺和数据库均可用；TikTok 仍为禁采。

- [x] **Step 2: 备份生产 SQLite**

Run: `.venv/bin/adwatch backup create`

Expected: 输出备份路径；随后运行 `.venv/bin/adwatch backup verify <输出路径>` 通过。

- [x] **Step 3: 第一次回溯**

Run: `.venv/bin/adwatch orders sync --platform shopee --from 2026-08-01 --to 2026-08-04`

Expected: 命令只读平台并输出订单、SKU、退款、物流、库存快照计数；不发送飞书。

- [x] **Step 4: 查询并保存基线计数**

Run:

```bash
.venv/bin/python -c "from adwatch.config import Settings; from adwatch.storage.db import Database; d=Database(Settings.from_env().database_path); c=d.connect(); print({name:c.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in ('platform_refund_observations','platform_logistics_observations','platform_inventory_snapshots')}); c.close()"
```

Expected: 三张表均可查询；库存快照大于 0；有真实退款订单时退款观测大于 0。

- [x] **Step 5: 第二次运行验证幂等**

再次运行 Step 3 和 Step 4。Expected: 同一观测日三张表的总行数不增加；运行记录新增一条且新增事实计数为 0，状态为 `success` 或仅因已知无 Seller SKU 记录为 `partial`。

- [x] **Step 6: 检查 PII 和会话泄漏**

Run:

```bash
rg -n "SPC_CDS|buyer_name|phone|address|cookie" var/logs var/reports || true
```

Expected: 没有会话令牌或买家 PII；若命中代码生成的固定字段说明，人工核对后在 `progress.md` 记录，不把误报视为泄漏。

- [x] **Step 7: 更新能力证据**

在 `progress.md` 记录实际范围、计数、幂等结果和缺失字段；只有真实验收满足完成门槛后，才在 `findings.md` 更新退款/物流/库存相关状态。

---

### Task 9: 文档、审查和完整质量门禁

**Files:**

- Modify: `README.md`
- Modify: `docs/operations/data-foundation.md`
- Modify: `docs/plans/2026-08-04-adwatch-remaining-gap-closure.md`
- Modify: `task_plan.md`
- Modify: `progress.md`

- [x] **Step 1: 更新文档事实**

README 必须说明：每天回溯配置天数；退款金额可能为 `missing`；物流和平台库存按观测日保存；TikTok 仍禁采。数据底座文档列出三张表的主键、来源时间、观测时间和保留策略。

- [x] **Step 2: 运行针对性审查**

检查：硬编码阈值、会话/PII 泄漏、跨日覆盖、取消单误当退款、同订单多 SKU 重复观测、数据库错误被静默吞掉、日任务失败未进入 partial。

- [x] **Step 3: 运行完整自动化验证**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/adwatch-p1a-pycache .venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
PYTHONPYCACHEPREFIX=/tmp/adwatch-p1a-pycache .venv/bin/python -m compileall -q src tests
git diff --check
```

Expected: 全部命令 exit 0；pytest 失败数为 0；Ruff 输出 `All checks passed!`。

- [x] **Step 4: 更新任务状态**

在 `task_plan.md` 将 P1-A 的需求、架构、实现、检测、测试、审查、文档和现场验收分别标记完成；`progress.md` 记录命令、输出计数和仍需 P1-B/P1-C 关闭的风险。

- [x] **Step 5: 保留工作区等待用户的 Git/部署指令**

不得自动 commit、push 或部署。若用户要求 Git，先按 `.trae/rules/git-workflow.md` 检查具体变更；若用户要求部署，先建立 AdWatch 项目专属部署计划并使用【服务器自动化部署】技能。

---

## P1-A 完成门槛

- 每日 Shopee 同步覆盖报告日及此前 3 天，天数可配置且有校验；
- 同一观测日重复运行不重复新增事实，跨日保留历史；
- 退款状态、物流状态和平台库存快照可按来源日期查询；
- 退款金额缺失时为 `NULL/missing`，没有任何路径把它转换为零；
- 取消订单不会自动生成退款事实；同订单多 SKU 只生成一条订单级观测；
- 同步失败保留历史数据并记录失败运行，日任务继续以 partial 生成广告报告；
- 真实 Shopee 回溯、第二次幂等运行、数据库备份和 PII 检查均有证据；
- 全量 pytest、Ruff、compileall 和 `git diff --check` 通过；
- README、数据底座、`findings.md`、`task_plan.md` 与实际能力一致。
