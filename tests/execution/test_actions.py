import inspect

from adwatch.execution.actions import ActionAdapter, ActionRegistry


def test_each_platform_action_has_a_dedicated_adapter():
    registry = ActionRegistry.default()

    adapters = {
        type(registry.get(platform, action))
        for platform in ("tiktok", "shopee")
        for action in (
            "increase_budget",
            "reduce_budget",
            "adjust_roas_target",
            "pause",
            "resume",
        )
    }

    assert len(adapters) == 10


def test_action_adapter_does_not_accept_arbitrary_script():
    for method_name in ("read", "stage", "submit", "capture"):
        parameters = inspect.signature(
            getattr(ActionAdapter, method_name)
        ).parameters
        assert "script" not in parameters
