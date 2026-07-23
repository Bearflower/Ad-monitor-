import pytest

from adwatch.approval.service import ApprovalService
from adwatch.execution.executor import ExecutionError, SafeExecutor
from adwatch.storage.db import Database


class FakeBackend:
    def __init__(self):
        self.current = {"budget": "100"}

    def read_current(self, recommendation):
        return dict(self.current)

    def execute(self, recommendation):
        self.current = {"budget": "70"}
        return dict(self.current)

    def capture(self, label):
        return f"/tmp/{label}.png"


def _approved(database):
    with database.transaction() as connection:
        recommendation_id = connection.execute(
            """
            INSERT INTO recommendations(
                rule_code, platform, campaign_id, sku_id, data_date, action,
                change_ratio, reason, requires_approval
            ) VALUES (
                'reduce', 'tiktok', 'campaign-1', 'SKU-1', '2026-07-22',
                'reduce_budget', '-0.30', 'low ROAS', 1
            )
            """
        ).lastrowid
    service = ApprovalService(database)
    request = service.create(recommendation_id)
    service.decide(
        request.approval_id, request.decision_token, approve=True, actor="boss"
    )
    return request.approval_id


def test_executor_records_success_and_rejects_duplicate(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    approval_id = _approved(database)
    executor = SafeExecutor(database, FakeBackend())
    result = executor.execute(
        approval_id,
        idempotency_key="operation-1",
        expected_before={"budget": "100"},
    )
    assert result.status == "succeeded"
    with pytest.raises(ExecutionError, match="already used"):
        executor.execute(
            approval_id,
            idempotency_key="operation-1",
            expected_before={"budget": "100"},
        )


def test_executor_rejects_state_drift(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    executor = SafeExecutor(database, FakeBackend())
    with pytest.raises(ExecutionError, match="drift"):
        executor.execute(
            _approved(database),
            idempotency_key="operation-2",
            expected_before={"budget": "90"},
        )
