from datetime import date
from decimal import Decimal

from adwatch.integrations.exchange_rates import (
    EcbExchangeRateSource,
    StaticExchangeRateSource,
)
from adwatch.integrations.inventory import StaticInventorySource
from adwatch.integrations.platform_api import UnconfiguredPlatformApi
from adwatch.integrations.refunds import StaticRefundSource


def test_refunds_support_t_plus_three_correction():
    source = StaticRefundSource(
        {("shopee", "campaign-1", date(2026, 7, 20)): Decimal(12)}
    )

    assert source.fetch(
        platform="shopee",
        campaign_id="campaign-1",
        data_date=date(2026, 7, 20),
        as_of=date(2026, 7, 23),
    ) == Decimal(12)


def test_inventory_and_exchange_sources_have_deterministic_contracts():
    inventory = StaticInventorySource(
        {("SKU-1", date(2026, 7, 23)): (100, Decimal(5))}
    )
    exchange = StaticExchangeRateSource(
        {("THB", date(2026, 7, 23)): Decimal("0.21")}
    )

    assert inventory.fetch("SKU-1", date(2026, 7, 23)) == (
        100,
        Decimal(5),
    )
    assert exchange.fetch("THB", date(2026, 7, 23)) == Decimal("0.21")


def test_unconfigured_official_api_is_pending_not_empty_success():
    result = UnconfiguredPlatformApi("tiktok").status()

    assert result.status == "pending_external"
    assert result.platform == "tiktok"


def test_ecb_source_calculates_cny_cross_rate_and_carries_weekends():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <Envelope xmlns="http://www.gesmes.org/xml/2002-08-01"
      xmlns:e="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
      <e:Cube>
        <e:Cube time="2026-07-24">
          <e:Cube currency="CNY" rate="7.7000"/>
          <e:Cube currency="THB" rate="38.5000"/>
        </e:Cube>
        <e:Cube time="2026-07-27">
          <e:Cube currency="CNY" rate="7.6500"/>
          <e:Cube currency="THB" rate="38.2500"/>
        </e:Cube>
      </e:Cube>
    </Envelope>"""
    source = EcbExchangeRateSource(fetcher=lambda _: xml)

    rates = source.fetch_range(
        "THB", date(2026, 7, 24), date(2026, 7, 27)
    )

    assert rates == {
        date(2026, 7, 24): Decimal("0.2"),
        date(2026, 7, 25): Decimal("0.2"),
        date(2026, 7, 26): Decimal("0.2"),
        date(2026, 7, 27): Decimal("0.2"),
    }
