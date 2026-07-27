from adwatch.execution.activation import SelectorActivationStore
from adwatch.storage.db import Database


def test_selector_activation_round_trips_field_evidence(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    store = SelectorActivationStore(database)

    store.register(
        platform="shopee",
        action="reduce_budget",
        selector_version="2026-07-27",
        selectors={
            "value": "[data-campaign='campaign-1'] input",
            "submit": "button[type='submit']",
        },
        store_id="store-1",
        activated_by="boss",
        evidence_before="/tmp/before.png",
        evidence_after="/tmp/after.png",
    )

    activation = store.get("shopee", "reduce_budget")

    assert activation is not None
    assert activation.selector_version == "2026-07-27"
    assert activation.selectors["submit"] == "button[type='submit']"
    assert store.list()[0] == activation
