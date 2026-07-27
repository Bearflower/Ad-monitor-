from datetime import date
from decimal import Decimal

from adwatch.strategy.rules import StrategyContext, recommend


def test_learning_campaign_never_receives_pause_action():
    result = recommend(
        StrategyContext.example(
            platform="tiktok",
            campaign_start=date(2026, 7, 18),
            data_date=date(2026, 7, 22),
            consecutive_low_days=4,
            roas=Decimal("0.2"),
            target_roas=Decimal("2.0"),
        )
    )
    assert all(item.action != "pause" for item in result)


def test_three_low_days_after_learning_recommends_pause():
    result = recommend(
        StrategyContext.example(
            platform="shopee",
            campaign_start=date(2026, 7, 1),
            data_date=date(2026, 7, 22),
            consecutive_low_days=3,
            roas=Decimal("0.8"),
            target_roas=Decimal("2.0"),
        )
    )
    assert [(item.action, item.requires_approval) for item in result] == [
        ("pause", True)
    ]


def test_negative_profit_or_stock_risk_blocks_budget_increase():
    context = StrategyContext.example(
        roas=Decimal("4"),
        target_roas=Decimal("2"),
        net_profit=Decimal("-1"),
        inventory_cover_days=Decimal("3"),
    )
    assert all(item.action != "increase_budget" for item in recommend(context))


def test_moderately_low_roas_recommends_lower_target_after_learning():
    result = recommend(
        StrategyContext.example(
            platform="shopee",
            roas=Decimal("1.5"),
            target_roas=Decimal("2"),
        )
    )

    assert [(item.action, item.change_ratio) for item in result] == [
        ("adjust_roas_target", Decimal("-0.20"))
    ]


def test_roas_above_one_fifty_percent_recommends_higher_target():
    result = recommend(
        StrategyContext.example(
            platform="shopee",
            roas=Decimal("3.2"),
            target_roas=Decimal("2"),
        )
    )

    assert [(item.action, item.change_ratio) for item in result] == [
        ("adjust_roas_target", Decimal("0.20"))
    ]


def test_product_retest_is_capped_at_twenty_percent():
    context = StrategyContext.example(
        retest_candidate=True,
        verified_profit=True,
        inventory_verified=True,
        available_test_budget=Decimal("300"),
        current_budget=Decimal("1000"),
        roas=Decimal("1"),
        target_roas=Decimal("2"),
    )

    retest = next(
        item for item in recommend(context) if item.action == "allocate_retest"
    )

    assert retest.amount == Decimal("200")
    assert retest.amount <= context.current_budget * Decimal("0.20")


def test_product_retest_requires_verified_profit_and_inventory():
    context = StrategyContext.example(
        retest_candidate=True,
        verified_profit=False,
        inventory_verified=False,
    )

    assert all(item.action != "allocate_retest" for item in recommend(context))
