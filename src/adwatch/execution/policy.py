from dataclasses import dataclass


class PolicyError(RuntimeError):
    pass


ALLOWED_ACTIONS = frozenset(
    {
        "increase_budget",
        "reduce_budget",
        "adjust_roas_target",
        "pause",
        "resume",
    }
)


@dataclass(frozen=True)
class ExecutionPolicy:
    live_writes: bool = False
    allowed_targets: frozenset[tuple[str, str, str]] = frozenset()

    def authorize(
        self,
        *,
        mode: str,
        platform: str,
        store_id: str,
        campaign_id: str,
        action: str,
    ) -> None:
        if action not in ALLOWED_ACTIONS:
            raise PolicyError(f"action is not allowed: {action}")
        if mode == "shadow":
            return
        if mode != "live":
            raise PolicyError(f"unsupported execution mode: {mode}")
        if not self.live_writes:
            raise PolicyError("live writes are disabled")
        target = (platform, store_id, campaign_id)
        if target not in self.allowed_targets:
            raise PolicyError("target is not in live allowlist")
