from adwatch.strategy.circuit_breaker import CircuitInputs, evaluate_circuit


def test_five_daily_alerts_open_the_circuit():
    result = evaluate_circuit(
        CircuitInputs(
            daily_alerts=5,
            webdriver_failures=0,
            quality_ok=True,
            consecutive_global_low_roas_days=0,
        )
    )
    assert result.is_open is True
    assert result.reasons == ("daily_alert_limit",)


def test_healthy_inputs_leave_circuit_closed():
    result = evaluate_circuit(CircuitInputs.healthy())
    assert result.is_open is False
