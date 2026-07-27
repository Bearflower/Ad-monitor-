import pytest

from adwatch.execution.policy import ExecutionPolicy, PolicyError
from adwatch.execution.ziniao_backend import ZiniaoExecutionBackend


class FakePageClient:
    def __init__(self):
        self.scripts = []

    def page_exec(self, store_id, script):
        self.scripts.append((store_id, script))
        if "ADWATCH_READ" in script:
            return {"budget": "100"}
        return {"budget": "70"}


def _recommendation():
    return {
        "platform": "shopee",
        "store_id": "store-1",
        "campaign_id": "campaign-1",
        "action": "reduce_budget",
        "change_ratio": "-0.30",
    }


def test_shadow_backend_reads_and_records_intent_without_submit():
    client = FakePageClient()
    backend = ZiniaoExecutionBackend(
        client,
        mode="shadow",
        policy=ExecutionPolicy(),
    )

    before = backend.read_current(_recommendation())
    after = backend.execute(_recommendation())

    assert before == {"budget": "100"}
    assert after == {
        "budget": "70.00",
        "mode": "shadow",
        "submitted": False,
    }
    assert all("ADWATCH_SUBMIT" not in script for _, script in client.scripts)


def test_live_backend_refuses_when_global_switch_is_off():
    backend = ZiniaoExecutionBackend(
        FakePageClient(),
        mode="live",
        policy=ExecutionPolicy(live_writes=False),
    )

    with pytest.raises(PolicyError, match="disabled"):
        backend.execute(_recommendation())
