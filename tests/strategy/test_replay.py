import json
from datetime import date

from adwatch.storage.db import Database
from adwatch.strategy.replay import StrategyReplayService
from adwatch.strategy.rules import StrategyContext


def test_strategy_replay_reconstructs_saved_context_and_matches_action(tmp_path):
    database = Database(tmp_path / "replay.sqlite3")
    database.migrate()
    context = StrategyContext.example(
        platform="shopee",
        roas=StrategyContext.example().roas / 4,
        target_roas=StrategyContext.example().target_roas,
        consecutive_low_days=3,
    )
    evidence = {
        "strategy_context": {
            key: (
                value.isoformat()
                if isinstance(value, date)
                else str(value)
                if hasattr(value, "as_tuple")
                else value
            )
            for key, value in context.__dict__.items()
        }
    }
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO recommendations(
                rule_code, platform, campaign_id, sku_id, data_date,
                action, reason, requires_approval, store_id,
                rule_version_id, evidence_json
            ) VALUES (
                'pause_sustained_low_roas', 'shopee', 'C-1', 'SKU-1',
                '2026-07-22', 'pause', 'saved', 1, 'shop',
                'default-v1', ?
            )
            """,
            (json.dumps(evidence, sort_keys=True),),
        )

    result = StrategyReplayService(database).replay(
        platform="shopee",
        store="shop",
        campaign_id="C-1",
        start=date(2026, 7, 22),
        end=date(2026, 7, 22),
    )

    assert result.status == "matched"
    assert result.checked == 1
