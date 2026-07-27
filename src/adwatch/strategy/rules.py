from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class StrategyContext:
    platform: str
    campaign_start: date
    data_date: date
    consecutive_low_days: int
    roas: Decimal
    target_roas: Decimal
    net_profit: Decimal
    inventory_cover_days: Decimal
    current_budget: Decimal
    baseline_budget: Decimal

    @classmethod
    def example(cls, **overrides: object) -> "StrategyContext":
        base = cls(
            platform="tiktok",
            campaign_start=date(2026, 7, 1),
            data_date=date(2026, 7, 22),
            consecutive_low_days=0,
            roas=Decimal("2"),
            target_roas=Decimal("2"),
            net_profit=Decimal("100"),
            inventory_cover_days=Decimal("30"),
            current_budget=Decimal("100"),
            baseline_budget=Decimal("100"),
        )
        return replace(base, **overrides)


@dataclass(frozen=True)
class Recommendation:
    rule_code: str
    action: str
    change_ratio: Decimal | None
    reason: str
    requires_approval: bool = True


def recommend(context: StrategyContext) -> tuple[Recommendation, ...]:
    learning_days = 7 if context.platform == "tiktok" else 14
    age_days = (context.data_date - context.campaign_start).days
    if age_days < learning_days:
        return ()

    target_ratio = (
        context.roas / context.target_roas
        if context.target_roas > 0
        else Decimal("0")
    )
    if target_ratio < Decimal("0.50") and context.consecutive_low_days >= 3:
        return (
            Recommendation(
                rule_code="pause_sustained_low_roas",
                action="pause",
                change_ratio=None,
                reason="ROAS stayed below 50% of target for three days",
            ),
        )
    if target_ratio < Decimal("0.70"):
        return (
            Recommendation(
                rule_code="reduce_budget_low_roas",
                action="reduce_budget",
                change_ratio=Decimal("-0.30"),
                reason="ROAS is below 70% of target after learning",
            ),
        )
    if target_ratio < Decimal("0.80"):
        return (
            Recommendation(
                rule_code="lower_roas_target",
                action="adjust_roas_target",
                change_ratio=Decimal("-0.20"),
                reason="ROAS remained below 80% of target after learning",
            ),
        )
    if target_ratio > Decimal("1.50"):
        return (
            Recommendation(
                rule_code="raise_roas_target",
                action="adjust_roas_target",
                change_ratio=Decimal("0.20"),
                reason="ROAS exceeded 150% of target after learning",
            ),
        )
    if (
        target_ratio >= Decimal("1")
        and context.net_profit > 0
        and context.inventory_cover_days >= 14
        and context.current_budget > 0
    ):
        maximum_ratio = (
            context.baseline_budget * 2 / context.current_budget
        ) - Decimal("1")
        change_ratio = min(Decimal("0.30"), maximum_ratio)
        if change_ratio > 0:
            return (
                Recommendation(
                    rule_code="increase_budget_profitable",
                    action="increase_budget",
                    change_ratio=change_ratio,
                    reason="ROAS is on target with profit and sufficient stock",
                ),
            )
    return ()
