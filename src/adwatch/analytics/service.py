from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

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
                required = (
                    "product_cost",
                    "commission_rate",
                    "inventory_units",
                    "expected_daily_units",
                    "rate_to_cny",
                    "start_date",
                    "target_roas",
                )
                missing = [field for field in required if row[field] is None]
                if missing:
                    self._upsert_alert(
                        connection,
                        row,
                        "missing_business_input",
                        "critical",
                        f"Missing business inputs: {', '.join(missing)}",
                    )
                    alert_count += 1
                    continue

                profit = calculate_profit(
                    ProfitInput(
                        gmv=Decimal(row["attributed_gmv"]),
                        refunds=Decimal(row["refund_amount"]),
                        commission_rate=Decimal(row["commission_rate"]),
                        product_cost=Decimal(row["product_cost"]),
                        ad_spend=Decimal(row["spend"]),
                        seller_shipping=Decimal(row["seller_shipping"]),
                        coupons=Decimal(row["coupons"]),
                        allocated_fixed_cost=Decimal(
                            row["allocated_fixed_cost"]
                        ),
                        exchange_rate_to_cny=Decimal(row["rate_to_cny"]),
                    )
                )
                self._upsert_profit(connection, row, profit)
                profit_count += 1

                expected_units = Decimal(row["expected_daily_units"])
                inventory_cover = (
                    Decimal(row["inventory_units"]) / expected_units
                    if expected_units > 0
                    else Decimal("0")
                )
                context = StrategyContext(
                    platform=row["platform"],
                    campaign_start=date.fromisoformat(row["start_date"]),
                    data_date=data_date,
                    consecutive_low_days=0,
                    roas=Decimal(row["roas"] or "0"),
                    target_roas=Decimal(row["target_roas"]),
                    net_profit=profit.net_profit_cny,
                    inventory_cover_days=inventory_cover,
                    current_budget=Decimal(row["current_budget"]),
                    baseline_budget=Decimal(row["baseline_budget"]),
                )
                for item in recommend(context):
                    connection.execute(
                        """
                        INSERT INTO recommendations(
                            rule_code, platform, campaign_id, sku_id, data_date,
                            action, change_ratio, reason, requires_approval
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(
                            rule_code, platform, campaign_id, sku_id, data_date
                        ) DO UPDATE SET
                            action=excluded.action,
                            change_ratio=excluded.change_ratio,
                            reason=excluded.reason,
                            requires_approval=excluded.requires_approval,
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
                        ),
                    )
                    recommendation_count += 1

            circuit = evaluate_circuit(
                CircuitInputs(
                    daily_alerts=alert_count,
                    webdriver_failures=0,
                    quality_ok=alert_count == 0,
                    consecutive_global_low_roas_days=0,
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
                        datetime.now(timezone.utc).isoformat()
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
        )

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
