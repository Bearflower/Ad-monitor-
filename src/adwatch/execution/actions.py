from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ActionAdapter(Protocol):
    state_key: str

    def read(
        self, client, store_id: str, campaign_id: str, selectors: dict[str, str]
    ) -> dict[str, str]: ...

    def stage(
        self,
        client,
        store_id: str,
        campaign_id: str,
        intended: dict[str, str],
        selectors: dict[str, str],
    ) -> None: ...

    def submit(
        self, client, store_id: str, campaign_id: str, selectors: dict[str, str]
    ) -> None: ...

    def capture(self, client, store_id: str, destination: Path) -> str: ...


class _DomAction:
    state_key = ""
    stage_kind = "input"

    @staticmethod
    def _selector(
        selectors: dict[str, str], name: str, campaign_id: str
    ) -> str:
        return selectors[name].format(campaign_id=campaign_id)

    def read(
        self, client, store_id: str, campaign_id: str, selectors: dict[str, str]
    ) -> dict[str, str]:
        value = client.page_query(
            store_id, self._selector(selectors, "value", campaign_id)
        )
        if isinstance(value, dict):
            value = value.get("value", value.get("text", ""))
        return {self.state_key: str(value)}

    def stage(
        self,
        client,
        store_id: str,
        campaign_id: str,
        intended: dict[str, str],
        selectors: dict[str, str],
    ) -> None:
        if selectors.get("open"):
            client.page_click(
                store_id, self._selector(selectors, "open", campaign_id)
            )
        selector = self._selector(selectors, "stage", campaign_id)
        if self.stage_kind == "click":
            client.page_click(store_id, selector)
        else:
            client.page_input(
                store_id, selector, intended[self.state_key], clear=True
            )

    def submit(
        self, client, store_id: str, campaign_id: str, selectors: dict[str, str]
    ) -> None:
        client.page_click(
            store_id, self._selector(selectors, "submit", campaign_id)
        )

    def capture(self, client, store_id: str, destination: Path) -> str:
        return client.page_screenshot(store_id, destination)


class TikTokIncreaseBudget(_DomAction):
    state_key = "budget"


class TikTokReduceBudget(_DomAction):
    state_key = "budget"


class TikTokAdjustRoasTarget(_DomAction):
    state_key = "target_roas"


class TikTokPause(_DomAction):
    state_key = "status"
    stage_kind = "click"


class TikTokResume(_DomAction):
    state_key = "status"
    stage_kind = "click"


class ShopeeIncreaseBudget(_DomAction):
    state_key = "budget"


class ShopeeReduceBudget(_DomAction):
    state_key = "budget"


class ShopeeAdjustRoasTarget(_DomAction):
    state_key = "target_roas"


class ShopeePause(_DomAction):
    state_key = "status"
    stage_kind = "click"


class ShopeeResume(_DomAction):
    state_key = "status"
    stage_kind = "click"


class ActionRegistry:
    def __init__(self, adapters: dict[tuple[str, str], ActionAdapter]) -> None:
        self.adapters = adapters

    @classmethod
    def default(cls) -> ActionRegistry:
        return cls(
            {
                ("tiktok", "increase_budget"): TikTokIncreaseBudget(),
                ("tiktok", "reduce_budget"): TikTokReduceBudget(),
                ("tiktok", "adjust_roas_target"): TikTokAdjustRoasTarget(),
                ("tiktok", "pause"): TikTokPause(),
                ("tiktok", "resume"): TikTokResume(),
                ("shopee", "increase_budget"): ShopeeIncreaseBudget(),
                ("shopee", "reduce_budget"): ShopeeReduceBudget(),
                ("shopee", "adjust_roas_target"): ShopeeAdjustRoasTarget(),
                ("shopee", "pause"): ShopeePause(),
                ("shopee", "resume"): ShopeeResume(),
            }
        )

    def get(self, platform: str, action: str) -> ActionAdapter:
        try:
            return self.adapters[(platform, action)]
        except KeyError as error:
            raise ValueError(
                f"no action adapter for {platform}/{action}"
            ) from error
