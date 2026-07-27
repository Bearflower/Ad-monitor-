import pytest

from adwatch.execution.actions import ActionRegistry
from adwatch.execution.activation import SelectorActivationStore
from adwatch.execution.policy import ExecutionPolicy, PolicyError
from adwatch.execution.ziniao_backend import ZiniaoExecutionBackend
from adwatch.storage.db import Database


class FakePageClient:
    def __init__(self):
        self.scripts = []
        self.state = {"budget": "100"}

    def page_exec(self, store_id, script):
        self.scripts.append((store_id, script))
        if "ADWATCH_READ" in script:
            return {"budget": "100"}
        return {"budget": "70"}

    def page_query(self, store_id, selector):
        self.scripts.append((store_id, f"query:{selector}"))
        return {"value": self.state["budget"]}

    def page_input(self, store_id, selector, text, *, clear=False):
        self.scripts.append((store_id, f"input:{selector}:{text}"))
        self.state["budget"] = text

    def page_click(self, store_id, selector):
        self.scripts.append((store_id, f"click:{selector}"))

    def page_screenshot(self, store_id, destination):
        self.scripts.append((store_id, f"screenshot:{destination}"))
        return str(destination)


def _recommendation():
    return {
        "platform": "shopee",
        "store_id": "store-1",
        "campaign_id": "campaign-1",
        "action": "reduce_budget",
        "change_ratio": "-0.30",
    }


def _activation_store(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    store = SelectorActivationStore(database)
    store.register(
        platform="shopee",
        action="reduce_budget",
        selector_version="v1",
        selectors={
            "value": "#budget-{campaign_id}",
            "stage": "#budget-{campaign_id}",
            "submit": "#submit-{campaign_id}",
        },
        store_id="store-1",
        activated_by="boss",
        evidence_before="/tmp/before.png",
        evidence_after="/tmp/after.png",
    )
    return store


def test_shadow_backend_reads_and_records_intent_without_submit(tmp_path):
    client = FakePageClient()
    backend = ZiniaoExecutionBackend(
        client,
        mode="shadow",
        policy=ExecutionPolicy(),
        activations=_activation_store(tmp_path),
    )

    before = backend.read_current(_recommendation())
    after = backend.execute(_recommendation())

    assert before == {"budget": "100"}
    assert after == {
        "budget": "70.00",
        "mode": "shadow",
        "submitted": False,
    }
    assert all(
        not script.startswith(("input:", "click:"))
        for _, script in client.scripts
    )


def test_live_backend_refuses_when_global_switch_is_off():
    backend = ZiniaoExecutionBackend(
        FakePageClient(),
        mode="live",
        policy=ExecutionPolicy(live_writes=False),
    )

    with pytest.raises(PolicyError, match="disabled"):
        backend.execute(_recommendation())


def test_live_rejects_inactive_selector_before_page_access(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    client = FakePageClient()
    backend = ZiniaoExecutionBackend(
        client,
        mode="live",
        policy=ExecutionPolicy(
            live_writes=True,
            allowed_targets=frozenset(
                {("shopee", "store-1", "campaign-1")}
            ),
        ),
        activations=SelectorActivationStore(database),
        registry=ActionRegistry.default(),
    )

    with pytest.raises(PolicyError, match="not field-activated"):
        backend.execute(_recommendation())

    assert client.scripts == []


def test_activated_live_action_uses_fixed_page_shortcuts(tmp_path):
    client = FakePageClient()
    backend = ZiniaoExecutionBackend(
        client,
        mode="live",
        policy=ExecutionPolicy(
            live_writes=True,
            allowed_targets=frozenset(
                {("shopee", "store-1", "campaign-1")}
            ),
        ),
        activations=_activation_store(tmp_path),
        registry=ActionRegistry.default(),
        screenshot_dir=tmp_path / "screenshots",
    )

    before = backend.read_current(_recommendation())
    screenshot = backend.capture("before")
    after = backend.execute(_recommendation())

    assert before == {"budget": "100"}
    assert after == {"budget": "70.00"}
    assert screenshot.endswith("before.png")
    assert any(
        script == "input:#budget-campaign-1:70.00"
        for _, script in client.scripts
    )
    assert any(
        script == "click:#submit-campaign-1"
        for _, script in client.scripts
    )
    assert all("ADWATCH_SUBMIT" not in script for _, script in client.scripts)


def test_activated_live_action_rolls_back_through_same_adapter(tmp_path):
    client = FakePageClient()
    backend = ZiniaoExecutionBackend(
        client,
        mode="live",
        policy=ExecutionPolicy(
            live_writes=True,
            allowed_targets=frozenset(
                {("shopee", "store-1", "campaign-1")}
            ),
        ),
        activations=_activation_store(tmp_path),
    )
    before = backend.read_current(_recommendation())
    backend.execute(_recommendation())

    restored = backend.rollback(_recommendation(), before)

    assert restored == {"budget": "100"}
