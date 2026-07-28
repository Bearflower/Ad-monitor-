from adwatch.approval.feishu import (
    build_approval_card,
    handle_approval_action,
)
from adwatch.approval.service import ApprovalService
from adwatch.storage.db import Database


def _request(database):
    with database.transaction() as connection:
        recommendation_id = connection.execute(
            """
            INSERT INTO recommendations(
                rule_code, platform, campaign_id, sku_id, data_date, action,
                change_ratio, reason, requires_approval
            ) VALUES (
                'reduce', 'shopee', 'Shop GMV Max', '__ALL__',
                '2026-07-23', 'reduce_budget', '-0.20', 'low ROAS', 1
            )
            """
        ).lastrowid
    return ApprovalService(database).create(recommendation_id)


def test_approval_card_contains_signed_approve_and_reject_actions(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    request = _request(database)

    payload = build_approval_card(
        request,
        platform="shopee",
        campaign_id="Shop GMV Max",
        action="reduce_budget",
        reason="low ROAS",
        evidence=("利润 ROAS 0.80", "库存可售 30"),
        expected_before={"budget": "100"},
        expected_after={"budget": "80"},
        web_url="http://127.0.0.1:8765/optimization",
    )

    assert payload["msg_type"] == "interactive"
    actions = payload["card"]["elements"][-1]["actions"]
    assert [item["value"]["decision"] for item in actions] == [
        "approve",
        "reject",
    ]
    assert all(
        item["value"]["decision_token"] == request.decision_token
        for item in actions
    )
    assert request.decision_token not in payload["card"]["header"]["title"]["content"]
    content = payload["card"]["elements"][0]["content"]
    assert "利润 ROAS 0.80" in content
    assert "100 → 80" in content
    assert "127.0.0.1:8765/optimization" in content


def test_feishu_action_decides_approval_using_operator_identity(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    service = ApprovalService(database)
    request = _request(database)
    action = {
        "operator": {"open_id": "ou_boss"},
        "action": {
            "value": {
                "approval_id": request.approval_id,
                "decision_token": request.decision_token,
                "decision": "approve",
            }
        },
    }

    decision = handle_approval_action(service, action)

    assert decision.status == "approved"
    assert decision.actor == "ou_boss"
