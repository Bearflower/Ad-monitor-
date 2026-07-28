from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from adwatch.collectors.ziniao_client import ZiniaoCliClient
from adwatch.execution.actions import ActionAdapter, ActionRegistry
from adwatch.execution.activation import (
    SelectorActivation,
    SelectorActivationStore,
)
from adwatch.execution.policy import ExecutionPolicy, PolicyError


class ZiniaoExecutionBackend:
    def __init__(
        self,
        client: ZiniaoCliClient,
        *,
        mode: str,
        policy: ExecutionPolicy,
        activations: SelectorActivationStore | None = None,
        registry: ActionRegistry | None = None,
        screenshot_dir: Path = Path("var/screenshots"),
    ) -> None:
        self.client = client
        self.mode = mode
        self.policy = policy
        self.activations = activations
        self.registry = registry or ActionRegistry.default()
        self.screenshot_dir = screenshot_dir
        self._before: dict[str, str] | None = None
        self._adapter: ActionAdapter | None = None
        self._activation: SelectorActivation | None = None
        self._store_id = ""

    def _resolve(
        self, recommendation: dict
    ) -> tuple[ActionAdapter, SelectorActivation]:
        platform = str(recommendation["platform"])
        action = str(recommendation["action"])
        activation = (
            None
            if self.activations is None
            else self.activations.get(platform, action)
        )
        if activation is None:
            raise PolicyError(
                f"{platform}/{action} is not field-activated"
            )
        if activation.store_id != str(recommendation["store_id"]):
            raise PolicyError("field activation does not match target store")
        return self.registry.get(platform, action), activation

    def read_current(self, recommendation: dict) -> dict:
        self.policy.authorize(
            mode="shadow" if self.mode == "shadow" else self.mode,
            platform=str(recommendation["platform"]),
            store_id=str(recommendation["store_id"]),
            campaign_id=str(recommendation["campaign_id"]),
            action=str(recommendation["action"]),
        )
        adapter, activation = self._resolve(recommendation)
        store_id = str(recommendation["store_id"])
        result = adapter.read(
            self.client,
            store_id,
            str(recommendation["campaign_id"]),
            activation.selectors,
        )
        self._adapter = adapter
        self._activation = activation
        self._store_id = store_id
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
            adapter, activation = self._resolved_state()
            adapter.stage(
                self.client,
                self._store_id,
                str(recommendation["campaign_id"]),
                intended,
                activation.selectors,
            )
            return {**intended, "mode": "shadow", "submitted": False}
        adapter, activation = self._resolved_state()
        campaign_id = str(recommendation["campaign_id"])
        adapter.stage(
            self.client,
            self._store_id,
            campaign_id,
            intended,
            activation.selectors,
        )
        adapter.submit(
            self.client,
            self._store_id,
            campaign_id,
            activation.selectors,
        )
        confirmed = adapter.read(
            self.client,
            self._store_id,
            campaign_id,
            activation.selectors,
        )
        if confirmed != intended:
            raise RuntimeError("advertising page did not confirm submitted state")
        return confirmed

    def capture(self, label: str) -> str:
        adapter, _ = self._resolved_state()
        destination = (self.screenshot_dir / f"{label}.png").resolve()
        self.screenshot_dir.resolve().mkdir(parents=True, exist_ok=True)
        if self.screenshot_dir.resolve() not in destination.parents:
            raise RuntimeError("screenshot path escaped configured directory")
        return adapter.capture(self.client, self._store_id, destination)

    def rollback(self, recommendation: dict, before: dict) -> dict:
        if self.mode == "shadow":
            return dict(before)
        adapter, activation = self._resolved_state()
        campaign_id = str(recommendation["campaign_id"])
        restored = {str(key): str(value) for key, value in before.items()}
        adapter.stage(
            self.client,
            self._store_id,
            campaign_id,
            restored,
            activation.selectors,
        )
        adapter.submit(
            self.client,
            self._store_id,
            campaign_id,
            activation.selectors,
        )
        confirmed = adapter.read(
            self.client,
            self._store_id,
            campaign_id,
            activation.selectors,
        )
        if confirmed != restored:
            raise RuntimeError("advertising page did not confirm rollback")
        return confirmed

    def _resolved_state(self) -> tuple[ActionAdapter, SelectorActivation]:
        if self._adapter is None or self._activation is None:
            raise RuntimeError("advertising state has not been read")
        return self._adapter, self._activation

    def _intended_state(self, recommendation: dict) -> dict[str, str]:
        before = self._before or {}
        action = str(recommendation["action"])
        if action in {"increase_budget", "reduce_budget"}:
            budget = Decimal(before["budget"])
            ratio = Decimal(str(recommendation["change_ratio"]))
            return {"budget": f"{budget * (Decimal(1) + ratio):.2f}"}
        if action == "adjust_roas_target":
            target = Decimal(before["target_roas"])
            ratio = Decimal(str(recommendation["change_ratio"]))
            return {"target_roas": f"{target * (Decimal(1) + ratio):.2f}"}
        if action == "pause":
            return {"status": "paused"}
        if action == "resume":
            return {"status": "active"}
        raise RuntimeError(f"unsupported action: {action}")
