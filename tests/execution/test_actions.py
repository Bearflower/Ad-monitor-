import inspect

from adwatch.execution.actions import (
    ActionAdapter,
    ActionRegistry,
    ShopeeReduceBudget,
)


class RecordingClient:
    def __init__(self):
        self.calls = []

    def page_click(self, store_id, selector):
        self.calls.append(("click", store_id, selector))

    def page_input(self, store_id, selector, text, *, clear=False):
        self.calls.append(("input", store_id, selector, text, clear))


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


def test_shopee_popover_action_opens_editor_before_staging_value():
    client = RecordingClient()
    ShopeeReduceBudget().stage(
        client,
        "store-1",
        "Shop GMV Max",
        {"budget": "180.00"},
        {
            "open": '[data-testid="budget-edit-popover-trigger"]',
            "value": (
                '[data-testid="budget-edit-panel-'
                'daily-budget-input-container"] input'
            ),
            "stage": (
                '[data-testid="budget-edit-panel-'
                'daily-budget-input-container"] input'
            ),
            "submit": (
                '[data-testid="budget-edit-popover"] '
                "button.eds-button--primary"
            ),
        },
    )

    assert client.calls == [
        (
            "click",
            "store-1",
            '[data-testid="budget-edit-popover-trigger"]',
        ),
        (
            "input",
            "store-1",
            (
                '[data-testid="budget-edit-panel-'
                'daily-budget-input-container"] input'
            ),
            "180.00",
            True,
        ),
    ]
