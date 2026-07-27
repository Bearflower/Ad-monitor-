import pytest

from adwatch.execution.policy import ExecutionPolicy, PolicyError


def test_live_policy_is_disabled_by_default():
    policy = ExecutionPolicy()

    with pytest.raises(PolicyError, match="disabled"):
        policy.authorize(
            mode="live",
            platform="shopee",
            store_id="store-1",
            campaign_id="campaign-1",
            action="reduce_budget",
        )


def test_policy_permanently_blocks_destructive_actions():
    policy = ExecutionPolicy(
        live_writes=True,
        allowed_targets=frozenset({("shopee", "store-1", "campaign-1")}),
    )

    with pytest.raises(PolicyError, match="not allowed"):
        policy.authorize(
            mode="live",
            platform="shopee",
            store_id="store-1",
            campaign_id="campaign-1",
            action="delete_campaign",
        )
