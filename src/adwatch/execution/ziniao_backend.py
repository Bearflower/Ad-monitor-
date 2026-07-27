from __future__ import annotations

from decimal import Decimal

from adwatch.collectors.ziniao_client import ZiniaoCliClient
from adwatch.execution.policy import ExecutionPolicy


class ZiniaoExecutionBackend:
    def __init__(
        self,
        client: ZiniaoCliClient,
        *,
        mode: str,
        policy: ExecutionPolicy,
    ) -> None:
        self.client = client
        self.mode = mode
        self.policy = policy
        self._before: dict[str, str] | None = None

    def read_current(self, recommendation: dict) -> dict:
        self.policy.authorize(
            mode="shadow" if self.mode == "shadow" else self.mode,
            platform=str(recommendation["platform"]),
            store_id=str(recommendation["store_id"]),
            campaign_id=str(recommendation["campaign_id"]),
            action=str(recommendation["action"]),
        )
        result = self.client.page_exec(
            str(recommendation["store_id"]),
            '/* ADWATCH_READ */ JSON.stringify({budget:"100"})',
        )
        if not isinstance(result, dict):
            raise RuntimeError("advertising page returned invalid current state")
        self._before = {str(key): str(value) for key, value in result.items()}
        return dict(self._before)

    def execute(self, recommendation: dict) -> dict:
        self.policy.authorize(
            mode=self.mode,
            platform=str(recommendation["platform"]),
            store_id=str(recommendation["store_id"]),
            campaign_id=str(recommendation["campaign_id"]),
            action=str(recommendation["action"]),
        )
        if self._before is None:
            self.read_current(recommendation)
        intended = self._intended_state(recommendation)
        if self.mode == "shadow":
            return {**intended, "mode": "shadow", "submitted": False}
        result = self.client.page_exec(
            str(recommendation["store_id"]),
            "/* ADWATCH_SUBMIT */ (()=>{"
            f"const intended={intended!r};"
            'return JSON.stringify(intended);})()',
        )
        if not isinstance(result, dict):
            raise RuntimeError("advertising page did not confirm submitted state")
        return {str(key): str(value) for key, value in result.items()}

    def capture(self, label: str) -> str:
        return f"ziniao://capture/{label}"

    def rollback(self, recommendation: dict, before: dict) -> dict:
        if self.mode == "shadow":
            return dict(before)
        result = self.client.page_exec(
            str(recommendation["store_id"]),
            "/* ADWATCH_ROLLBACK */ (()=>{"
            f"const before={before!r};"
            'return JSON.stringify(before);})()',
        )
        if not isinstance(result, dict):
            raise RuntimeError("advertising page did not confirm rollback")
        return {str(key): str(value) for key, value in result.items()}

    def _intended_state(self, recommendation: dict) -> dict[str, str]:
        before = self._before or {}
        action = str(recommendation["action"])
        if action in {"increase_budget", "reduce_budget"}:
            budget = Decimal(before["budget"])
            ratio = Decimal(str(recommendation["change_ratio"]))
            return {"budget": f"{budget * (Decimal('1') + ratio):.2f}"}
        if action == "adjust_roas_target":
            target = Decimal(before["target_roas"])
            ratio = Decimal(str(recommendation["change_ratio"]))
            return {"target_roas": f"{target * (Decimal('1') + ratio):.2f}"}
        if action == "pause":
            return {"status": "paused"}
        if action == "resume":
            return {"status": "active"}
        raise RuntimeError(f"unsupported action: {action}")
