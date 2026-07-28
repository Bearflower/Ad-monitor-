from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from adwatch.analytics.anomalies import detect_anomalies
from adwatch.analytics.profit import ProfitInput, calculate_profit
from adwatch.storage.analytics import AnalyticsRepository
from adwatch.storage.db import Database
from adwatch.strategy.circuit_breaker import CircuitInputs, evaluate_circuit
from adwatch.strategy.rules import StrategyContext, recommend


@dataclass(frozen=True)
class AnalysisSummary:
    metrics_processed: int
    profit_results: int
    alerts: int
    recommendations: int
    circuit_open: bool
    pending_data: int = 0
    capabilities: dict[str, str] = field(default_factory=dict)


class AnalysisService:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.repository = AnalyticsRepository(database)

    def seed_mock_business_data(self, data_date: date) -> int:
        return self.repository.seed_mock_business_data(data_date)

    def run(self, data_date: date) -> AnalysisSummary:
        rows = self.repository.load_analysis_rows(data_date)
        profit_count = 0
        alert_count = 0
        recommendation_count = 0
        pending_data_count = 0
        operational_alert_count = 0
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM profit_results WHERE data_date=?",
                (data_date.isoformat(),),
            )
            connection.execute(
                "DELETE FROM alerts WHERE data_date=?",
                (data_date.isoformat(),),
            )
            for row in rows:
                baseline = self.repository.baseline_for(row, data_date)
                for anomaly in detect_anomalies(
                    current_spend=Decimal(row["spend"]),
                    baseline_spend=(
                        None
                        if baseline is None or baseline["spend"] is None
                        else Decimal(str(baseline["spend"]))
                    ),
                    current_roas=(
                        None
                        if row["roas"] is None
                        else Decimal(row["roas"])
                    ),
                    baseline_roas=(
                        None
                        if baseline is None or baseline["roas"] is None
                        else Decimal(str(baseline["roas"]))
                    ),
                    inventory_units=int(row["inventory_units"] or 0),
                    expected_daily_units=Decimal(
                        row["expected_daily_units"] or "0"
                    ),
                    platform=row["platform"],
                    campaign_start=(
                        None
                        if row["start_date"] is None
                        else date.fromisoformat(row["start_date"])
                    ),
                    data_date=data_date,
                ):
                    self._upsert_alert(
                        connection,
                        row,
                        anomaly.code,
                        anomaly.severity,
                        anomaly.message,
                    )
                    alert_count += 1
                    operational_alert_count += 1
                if row["order_cost_allocation_ambiguous"]:
                    self._upsert_alert(
                        connection,
                        row,
                        "ambiguous_order_cost_allocation",
                        "info",
                        "Order cost matches multiple ad metric rows",
                    )
                    alert_count += 1
                    pending_data_count += 1
                    continue
                required = (
                    "product_cost",
                    "commission_rate",
                    "inventory_units",
                    "expected_daily_units",
                    "rate_to_cny",
                    "start_date",
                    "target_roas",
                )
                has_order_cost = row["order_product_cost_cny"] is not None
                missing = [
                    field
                    for field in required
                    if row[field] is None
                    and not (field == "product_cost" and has_order_cost)
                ]
                if missing:
                    self._upsert_alert(
                        connection,
                        row,
                        "missing_business_input",
                        "info",
                        f"Missing business inputs: {', '.join(missing)}",
                    )
                    alert_count += 1
                    pending_data_count += 1
                    continue

                profit = calculate_profit(
                    ProfitInput(
                        gmv=Decimal(row["attributed_gmv"]),
                        refunds=Decimal(row["refund_amount"]),
                        commission_rate=Decimal(row["commission_rate"]),
                        product_cost=Decimal(row["product_cost"] or "0"),
                        ad_spend=Decimal(row["spend"]),
                        seller_shipping=Decimal(row["seller_shipping"]),
                        coupons=Decimal(row["coupons"]),
                        allocated_fixed_cost=Decimal(
                            row["allocated_fixed_cost"]
                        ),
                        exchange_rate_to_cny=Decimal(row["rate_to_cny"]),
                        product_cost_cny=(
                            None
                            if row["order_product_cost_cny"] is None
                            else Decimal(row["order_product_cost_cny"])
                        ),
                    )
                )
                self._upsert_profit(connection, row, profit)
                profit_count += 1

                expected_units = Decimal(row["expected_daily_units"])
                inventory_cover = (
                    Decimal(row["inventory_units"]) / expected_units
                    if expected_units > 0
                    else Decimal(0)
                )
                context = StrategyContext(
                    platform=row["platform"],
                    campaign_start=date.fromisoformat(row["start_date"]),
                    data_date=data_date,
                    consecutive_low_days=(
                        self.repository.consecutive_campaign_low_days(
                            row, data_date
                        )
                    ),
                    roas=Decimal(row["roas"] or "0"),
                    target_roas=Decimal(row["target_roas"]),
                    net_profit=profit.net_profit_cny,
                    inventory_cover_days=inventory_cover,
                    current_budget=Decimal(row["current_budget"]),
                    baseline_budget=Decimal(row["baseline_budget"]),
                    retest_candidate=bool(row["retest_candidate"]),
                    verified_profit=True,
                    inventory_verified=True,
                    available_test_budget=Decimal(
                        row["available_test_budget"] or "0"
                    ),
                    net_sales_roas=(
                        None
                        if Decimal(row["spend"]) <= 0
                        else (
                            Decimal(row["attributed_gmv"])
                            - Decimal(row["refund_amount"])
                        )
                        / Decimal(row["spend"])
                    ),
                    profit_roas=(
                        None
                        if Decimal(row["spend"]) <= 0
                        else (
                            profit.net_profit_cny
                            + Decimal(row["spend"])
                            * Decimal(row["rate_to_cny"])
                        )
                        / (
                            Decimal(row["spend"])
                            * Decimal(row["rate_to_cny"])
                        )
                    ),
                    refund_rate=(
                        Decimal(0)
                        if Decimal(row["attributed_gmv"]) <= 0
                        else Decimal(row["refund_amount"])
                        / Decimal(row["attributed_gmv"])
                    ),
                    data_confidence="inventory_safe",
                    rule_version_id="default-v1",
                )
                for item in recommend(context):
                    evidence = {
                        "strategy_context": {
                            key: (
                                value.isoformat()
                                if isinstance(value, date)
                                else str(value)
                                if isinstance(value, Decimal)
                                else value
                            )
                            for key, value in context.__dict__.items()
                        }
                    }
                    connection.execute(
                        """
                        INSERT INTO recommendations(
                            rule_code, platform, campaign_id, sku_id, data_date,
                            action, change_ratio, reason, requires_approval,
                            store_id, amount, rule_version_id, window_days,
                            confidence_level, evidence_json,
                            expected_before_json, expected_impact_json
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        ON CONFLICT(
                            rule_code, platform, campaign_id, sku_id, data_date
                        ) DO UPDATE SET
                            action=excluded.action,
                            change_ratio=excluded.change_ratio,
                            reason=excluded.reason,
                            requires_approval=excluded.requires_approval,
                            store_id=excluded.store_id,
                            amount=excluded.amount,
                            rule_version_id=excluded.rule_version_id,
                            window_days=excluded.window_days,
                            confidence_level=excluded.confidence_level,
                            evidence_json=excluded.evidence_json,
                            expected_before_json=excluded.expected_before_json,
                            expected_impact_json=excluded.expected_impact_json,
                            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        """,
                        (
                            item.rule_code,
                            row["platform"],
                            row["campaign_id"],
                            row["sku_id"],
                            data_date.isoformat(),
                            item.action,
                            (
                                None
                                if item.change_ratio is None
                                else str(item.change_ratio)
                            ),
                            item.reason,
                            int(item.requires_approval),
                            row["account_id"],
                            (
                                None
                                if item.amount is None
                                else f"{item.amount:.2f}"
                            ),
                            context.rule_version_id,
                            7,
                            context.data_confidence,
                            json.dumps(evidence, sort_keys=True),
                            json.dumps(
                                {
                                    "budget": str(context.current_budget),
                                    "target_roas": str(context.target_roas),
                                },
                                sort_keys=True,
                            ),
                            json.dumps(
                                {
                                    "action": item.action,
                                    "change_ratio": (
                                        None
                                        if item.change_ratio is None
                                        else str(item.change_ratio)
                                    ),
                                },
                                sort_keys=True,
                            ),
                        ),
                    )
                    recommendation_count += 1

            circuit = evaluate_circuit(
                CircuitInputs(
                    daily_alerts=operational_alert_count,
                    webdriver_failures=self.repository.recent_webdriver_failures(),
                    quality_ok=True,
                    consecutive_global_low_roas_days=(
                        self.repository.consecutive_global_low_roas_days(
                            data_date
                        )
                    ),
                )
            )
            connection.execute(
                """
                UPDATE circuit_state
                SET is_open=?, reasons_json=?, opened_at=?
                WHERE id=1
                """,
                (
                    int(circuit.is_open),
                    json.dumps(circuit.reasons),
                    (
                        datetime.now(UTC).isoformat()
                        if circuit.is_open
                        else None
                    ),
                ),
            )

        return AnalysisSummary(
            metrics_processed=len(rows),
            profit_results=profit_count,
            alerts=alert_count,
            recommendations=recommendation_count,
            circuit_open=circuit.is_open,
            pending_data=pending_data_count,
            capabilities=self._capabilities(rows),
        )

    @staticmethod
    def _capabilities(rows) -> dict[str, str]:
        has_metrics = bool(rows)
        has_costs = has_metrics and all(
            row["product_cost"] is not None
            or row["order_product_cost_cny"] is not None
            for row in rows
        )
        verified_fields = (
            "product_cost",
            "commission_rate",
            "seller_shipping",
            "coupons",
            "allocated_fixed_cost",
            "refund_amount",
            "rate_to_cny",
            "start_date",
            "target_roas",
        )
        has_verified_profit = has_metrics and all(
            all(
                row[field] is not None
                or (
                    field == "product_cost"
                    and row["order_product_cost_cny"] is not None
                )
                for field in verified_fields
            )
            for row in rows
        )
        has_inventory = has_metrics and all(
            row["inventory_units"] is not None
            and row["expected_daily_units"] is not None
            and Decimal(row["expected_daily_units"]) > 0
            for row in rows
        )
        return {
            "platform_metrics": "ready" if has_metrics else "pending_data",
            "estimated_profit": "ready" if has_costs else "pending_data",
            "verified_profit": (
                "ready" if has_verified_profit else "pending_data"
            ),
            "inventory_safe_strategy": (
                "ready"
                if has_verified_profit and has_inventory
                else "pending_data"
            ),
        }

    @staticmethod
    def _upsert_profit(connection, row, profit) -> None:
        connection.execute(
            """
            INSERT INTO profit_results(
                platform, store, account_id, campaign_id, sku_id, data_date,
                net_sales_cny, platform_commission_cny, gross_profit_cny,
                net_profit_cny, break_even_roas
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(
                platform, store, account_id, campaign_id, sku_id, data_date
            ) DO UPDATE SET
                net_sales_cny=excluded.net_sales_cny,
                platform_commission_cny=excluded.platform_commission_cny,
                gross_profit_cny=excluded.gross_profit_cny,
                net_profit_cny=excluded.net_profit_cny,
                break_even_roas=excluded.break_even_roas,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (
                row["platform"],
                row["store"],
                row["account_id"],
                row["campaign_id"],
                row["sku_id"],
                row["data_date"],
                str(profit.net_sales_cny),
                str(profit.platform_commission_cny),
                str(profit.gross_profit_cny),
                str(profit.net_profit_cny),
                (
                    None
                    if profit.break_even_roas is None
                    else str(profit.break_even_roas)
                ),
            ),
        )

    @staticmethod
    def _upsert_alert(
        connection, row, code: str, severity: str, message: str
    ) -> None:
        connection.execute(
            """
            INSERT INTO alerts(
                rule_code, platform, campaign_id, sku_id, data_date,
                severity, message
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(
                rule_code, platform, campaign_id, sku_id, data_date
            ) DO UPDATE SET
                severity=excluded.severity,
                message=excluded.message,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (
                code,
                row["platform"],
                row["campaign_id"],
                row["sku_id"],
                row["data_date"],
                severity,
                message,
            ),
        )
