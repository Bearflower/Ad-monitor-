from datetime import datetime

from adwatch.orders.shopee_parser import parse_product_page


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
        observed_at=datetime(2026, 7, 28, 9, 0),
    )

    assert [(item.seller_sku, item.inventory_units) for item in result.skus] == [
        ("Foot Soak Bag-one bag", 31),
        ("Foot Soak Bag-two bags", 45),
        ("Foot Soak Bag-three bags", 48),
    ]
    assert result.skus[0].item_id == "57861884313"
    assert result.skus[0].variation_name == "1 bag"
