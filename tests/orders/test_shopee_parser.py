import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from adwatch.orders.shopee_parser import (
    parse_order_cards,
    parse_product_page,
    parse_product_payload,
)

COMMERCE_FIXTURE = Path(
    "tests/fixtures/shopee/commerce_order_cards.json"
)


def test_commerce_fixture_contains_only_required_non_pii_fields():
    payload = json.loads(COMMERCE_FIXTURE.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    for forbidden in (
        "buyer_name",
        "phone",
        "address",
        "spc_cds",
        "cookie",
    ):
        assert forbidden not in serialized
    card = payload[0]["order_card"]
    assert card["card_header"]["order_sn"] == "260801REFUND01"
    assert card["status_info"]["status"] == "Return/Refund"
    assert card["order_ext_info"]["logistics_status"] == 5


def test_parse_product_page_extracts_seller_skus_and_inventory():
    text = """
    สมุนไพรจีนแช่เท้า 30 ซอง สปาเท้า
    Parent SKU: - Item ID: 57861884313 AMS Commission
    1 bag SKU: Foot Soak Bag-one bag Model ID: 311033956020 31 Sales 16
    3 bags SKU: Foot Soak Bag-two bags Model ID: 311033956021 45 Sales 4
    5 bags SKU: Foot Soak Bag-three bags Model ID: 311033956022 48 Sales 2
    """

    result = parse_product_page(
        text,
        store="虾皮泰国",
        observed_at=datetime(2026, 7, 28, 9, 0, tzinfo=UTC),
    )

    assert [(item.seller_sku, item.inventory_units) for item in result.skus] == [
        ("Foot Soak Bag-one bag", 31),
        ("Foot Soak Bag-two bags", 45),
        ("Foot Soak Bag-three bags", 48),
    ]
    assert result.skus[0].item_id == "57861884313"
    assert result.skus[0].variation_name == "1 bag"


def test_parse_product_payload_extracts_all_models_without_page_text():
    observed_at = datetime(2026, 8, 4, 9, tzinfo=UTC)
    payload = {
        "data": {
            "products": [
                {
                    "id": 123,
                    "name": "Product A",
                    "model_list": [
                        {
                            "id": 456,
                            "name": "Blue",
                            "sku": "SELLER-A",
                            "stock_detail": {"total_available_stock": 12},
                        }
                    ],
                }
            ]
        }
    }

    result = parse_product_payload(
        payload,
        store="虾皮泰国",
        observed_at=observed_at,
    )

    assert result.rejected == ()
    assert result.skus[0].item_id == "123"
    assert result.skus[0].model_id == "456"
    assert result.skus[0].seller_sku == "SELLER-A"
    assert result.skus[0].inventory_units == 12


def test_parse_product_payload_rejects_negative_inventory():
    result = parse_product_payload(
        {
            "data": {
                "products": [
                    {
                        "id": 123,
                        "name": "Product A",
                        "model_list": [
                            {
                                "id": 456,
                                "name": "Blue",
                                "sku": "SELLER-A",
                                "stock_detail": {
                                    "total_available_stock": -1
                                },
                            }
                        ],
                    }
                ]
            }
        },
        store="虾皮泰国",
        observed_at=datetime(2026, 8, 4, 9, tzinfo=UTC),
    )

    assert result.skus == ()
    assert result.rejected == (
        "product 123 model 456 has invalid stock",
    )


def test_parse_order_cards_joins_item_model_and_preserves_total_paid():
    observed_at = datetime(2026, 8, 4, 9, tzinfo=UTC)
    cards = [
        {
            "order_card": {
                "card_header": {"order_sn": "260801ABC123"},
                "payment_info": {"total_price": 30000000, "currency": 7},
                "status_info": {"status": "Completed"},
                "order_ext_info": {"order_id": 999, "logistics_status": 5},
                "item_info_group": {
                    "item_info_list": [
                        {
                            "item_list": [
                                {
                                    "name": "Product A",
                                    "description": "Variation: Blue",
                                    "amount": 1,
                                    "inner_item_ext_info": {
                                        "item_id": 123,
                                        "model_id": 456,
                                    },
                                }
                            ]
                        },
                        {
                            "item_list": [
                                {
                                    "name": "Product B",
                                    "description": "Variation: Red",
                                    "amount": 2,
                                    "inner_item_ext_info": {
                                        "item_id": 124,
                                        "model_id": 457,
                                    },
                                }
                            ]
                        },
                    ]
                },
                "package_ext_info_list": [{"fulfilment_status": 4}],
            }
        }
    ]
    sku_map = {
        ("123", "456"): ("SELLER-A", "Blue", "Product A"),
        ("124", "457"): ("SELLER-B", "Red", "Product B"),
    }

    result = parse_order_cards(
        cards,
        store="虾皮泰国",
        observed_at=observed_at,
        sku_map=sku_map,
        start=date(2026, 8, 1),
        end=date(2026, 8, 1),
    )

    assert [line.seller_sku for line in result.orders] == [
        "SELLER-A",
        "SELLER-B",
    ]
    assert sum((line.buyer_paid for line in result.orders), Decimal(0)) == Decimal(
        "300.00"
    )
    assert result.orders[1].quantity == 2
    assert result.orders[0].ordered_at == date(2026, 8, 1)


def test_parse_order_cards_keeps_cancelled_status_but_rejects_unmapped_items():
    cards = [
        {
            "order_card": {
                "card_header": {"order_sn": "260801CANCEL"},
                "payment_info": {"total_price": 10000000},
                "status_info": {"status": "Cancelled"},
                "order_ext_info": {"order_id": 1, "logistics_status": 0},
                "item_info_group": {
                    "item_info_list": [
                        {
                            "item_list": [
                                {
                                    "name": "Unknown",
                                    "amount": 1,
                                    "inner_item_ext_info": {
                                        "item_id": 7,
                                        "model_id": 8,
                                    },
                                }
                            ]
                        }
                    ]
                },
            }
        }
    ]

    result = parse_order_cards(
        cards,
        store="虾皮泰国",
        observed_at=datetime(2026, 8, 4, 9, tzinfo=UTC),
        sku_map={},
        start=date(2026, 8, 1),
        end=date(2026, 8, 1),
    )

    assert result.orders == ()
    assert "unmapped item/model 7/8" in result.rejected[0]


def test_parse_order_cards_supports_package_level_cards():
    observed_at = datetime(2026, 8, 4, 9, tzinfo=UTC)
    cards = [
        {
            "package_level_order_card": {
                "card_header": {"order_sn": "260802PACKAGE"},
                "order_ext_info": {
                    "order_id": 999,
                    "logistics_status": 2,
                },
                "package_list": [
                    {
                        "payment_info": {"total_price": 12000000},
                        "status_info": {"status": "Shipped"},
                        "package_ext_info": {"fulfilment_status": 2},
                        "item_info_group": {
                            "item_info_list": [
                                {
                                    "item_list": [
                                        {
                                            "name": "Product A",
                                            "amount": 1,
                                            "inner_item_ext_info": {
                                                "item_id": 123,
                                                "model_id": 456,
                                            },
                                        }
                                    ]
                                }
                            ]
                        },
                    }
                ],
            }
        }
    ]

    result = parse_order_cards(
        cards,
        store="虾皮泰国",
        observed_at=observed_at,
        sku_map={("123", "456"): ("SELLER-A", "Blue", "Product A")},
        start=date(2026, 8, 2),
        end=date(2026, 8, 2),
    )

    assert result.rejected == ()
    assert len(result.orders) == 1
    assert result.orders[0].order_id == "260802PACKAGE"
    assert result.orders[0].buyer_paid == Decimal("120.00")
