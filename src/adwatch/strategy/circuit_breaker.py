from dataclasses import dataclass


@dataclass(frozen=True)
class CircuitInputs:
    daily_alerts: int
    webdriver_failures: int
    quality_ok: bool
    consecutive_global_low_roas_days: int

    @classmethod
    def healthy(cls) -> "CircuitInputs":
        return cls(
            daily_alerts=0,
            webdriver_failures=0,
            quality_ok=True,
            consecutive_global_low_roas_days=0,
        )


@dataclass(frozen=True)
class CircuitDecision:
    is_open: bool
    reasons: tuple[str, ...]


def evaluate_circuit(inputs: CircuitInputs) -> CircuitDecision:
    reasons: list[str] = []
    if inputs.daily_alerts >= 5:
        reasons.append("daily_alert_limit")
    if inputs.webdriver_failures >= 3:
        reasons.append("webdriver_failure_limit")
    if not inputs.quality_ok:
        reasons.append("core_quality_failure")
    if inputs.consecutive_global_low_roas_days >= 2:
        reasons.append("global_low_roas")
    return CircuitDecision(is_open=bool(reasons), reasons=tuple(reasons))
