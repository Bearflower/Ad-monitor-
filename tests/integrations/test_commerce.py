from datetime import date
from decimal import Decimal

import pytest

from adwatch.integrations.commerce import (
    AdFundingRecord,
    AdSpendRecord,
    CapabilityResult,
    CapabilityStatus,
    CommerceRepository,
    LogisticsRecord,
    PlatformFeeRecord,
    RefundRecord,
    SettlementRecord,
)
from adwatch.storage.db import Database


def test_capability_result_distinguishes_no_data_from_external_blocker():
    assert CapabilityResult.ready(()).status is CapabilityStatus.READY
    pending = CapabilityResult.pending_external("平台未开放接口")
    assert pending.status is CapabilityStatus.PENDING_EXTERNAL
    assert pending.reason == "平台未开放接口"


def test_commerce_contracts_keep_recharge_spend_refund_logistics_and_fee_distinct():
    assert AdFundingRecord.__name__ != AdSpendRecord.__name__
    assert {
        RefundRecord.__name__,
        LogisticsRecord.__name__,
        PlatformFeeRecord.__name__,
    } == {"RefundRecord", "LogisticsRecord", "PlatformFeeRecord"}


def test_settlement_import_is_idempotent_and_preserves_platform_fact(tmp_path):
    database = Database(tmp_path / "commerce.sqlite3")
    database.migrate()
    repository = CommerceRepository(database)
    record = SettlementRecord(
        external_key="shopee:shop:ORDER-1",
        platform="shopee",
        store="shop",
        order_id="ORDER-1",
        settled_on=date(2026, 7, 28),
        amount_original=Decimal("120.50"),
        currency="THB",
        rate_to_cny=Decimal("0.21"),
        source="ziniao_cli",
    )

    assert repository.upsert_settlements((record,)) == 1
    assert repository.upsert_settlements((record,)) == 0
    with pytest.raises(ValueError, match="immutable"):
        repository.upsert_settlements(
            (record.__class__(**{**record.__dict__, "amount_original": Decimal(1)}),)
        )
