import pytest

from adwatch.approval.service import ApprovalError, ApprovalService
from adwatch.storage.db import Database


def _recommendation(database):
    with database.transaction() as connection:
        cursor = connection.execute(
            """
            INSERT INTO recommendations(
                rule_code, platform, campaign_id, sku_id, data_date, action,
                change_ratio, reason, requires_approval
            ) VALUES (
                'reduce', 'tiktok', 'campaign-1', 'SKU-1', '2026-07-22',
                'reduce_budget', '-0.30', 'low ROAS', 1
            )
            """
        )
        return cursor.lastrowid


def test_approval_requires_valid_token_and_is_terminal(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    service = ApprovalService(database)
    request = service.create(_recommendation(database))

    with pytest.raises(ApprovalError, match="token"):
        service.decide(request.approval_id, "wrong", approve=True, actor="boss")

    approved = service.decide(
        request.approval_id,
        request.decision_token,
        approve=True,
        actor="boss",
    )
    assert approved.status == "approved"
    with pytest.raises(ApprovalError, match="already decided"):
        service.decide(
            request.approval_id,
            request.decision_token,
            approve=False,
            actor="boss",
        )
