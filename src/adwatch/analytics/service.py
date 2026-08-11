from __future__ import annotations

import json
from contextlib import nullcontext
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


@dataclass(frozen=True)
class InputLineage:
    input_name: str
    value_text: str | None
    status: str
    source_type: str
    source_date: str | None
    age_days: int | None
    source_ref: str = ""
    detail_code: str = ""


class AnalysisService:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.repository = AnalyticsRepository(database)

    def seed_mock_business_data(self, data_date: date) -> int:
        return self.repository.seed_mock_business_data(data_date)

    def run(self, data_date: date, *, _connection=None) -> AnalysisSummary:
        profit_count = 0
        alert_count = 0
        recommendation_count = 0
        pending_data_count = 0
        operational_alert_count = 0
        transaction = (
            self.database.transaction()
            if _connection is None
            else nullcontext(_connection)
        )
        with transaction as connection:
            rows = self.repository.load_analysis_rows(
                data_date, connection=connection
            )
            connection.execute(
                "DELETE FROM profit_results WHERE data_date=?",
                (data_date.isoformat(),),
            )
            connection.execute(
                "DELETE FROM alerts WHERE data_date=?",
                (data_date.isoformat(),),
            )
            connection.execute(
                "DELETE FROM recommendations WHERE data_date=?",
                (data_date.isoformat(),),
            )
            connection.execute(
                "DELETE FROM profit_input_lineage WHERE data_date=?",
                (data_date.isoformat(),),
            )
            for row in rows:
                inventory_required = bool(row["inventory_required"])
                lineage = self._profit_input_lineage(row, data_date)
                self._insert_lineage(connection, row, lineage)
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
                    inventory_units=(
                        int(row["inventory_units"] or 0)
                        if inventory_required
                        else 0
                    ),
                    expected_daily_units=Decimal(
                        row["expected_daily_units"] or "0"
                        if inventory_required
                        else "0"
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
                lineage_missing_names = {
                    item.input_name
                    for item in lineage
                    if item.status in {"pending_data", "ambiguous"}
                }
                financial_missing = [
                    {
                        "refunds": "refund_amount",
                        "commission_rate": "resolved_commission_rate",
                        "exchange_rate": "rate_to_cny",
                    }.get(name, name)
                    for name in sorted(lineage_missing_names)
                ]
                if financial_missing:
                    self._upsert_alert(
                        connection,
                        row,
                        "missing_business_input",
                        "info",
                        "Missing business inputs: "
                        f"{', '.join(financial_missing)}",
                    )
                    alert_count += 1
                    pending_data_count += 1
                    continue

                profit = calculate_profit(
                    ProfitInput(
                        gmv=Decimal(row["attributed_gmv"]),
                        refunds=Decimal(row["refund_amount"]),
                        commission_rate=Decimal(
                            row["resolved_commission_rate"]
                        ),
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

                strategy_required = ["start_date", "target_roas"]
                if inventory_required:
                    strategy_required.extend(
                        ("inventory_units", "expected_daily_units")
                    )
                strategy_missing = [
                    field
                    for field in strategy_required
                    if row[field] is None
                ]
                if strategy_missing:
                    self._upsert_alert(
                        connection,
                        row,
                        "missing_strategy_input",
                        "info",
                        "Missing strategy inputs: "
                        f"{', '.join(strategy_missing)}",
                    )
                    alert_count += 1
                    pending_data_count += 1
                    continue

                if inventory_required:
                    expected_units = Decimal(row["expected_daily_units"])
                    inventory_cover = (
                        Decimal(row["inventory_units"]) / expected_units
                        if expected_units > 0
                        else Decimal(0)
                    )
                else:
                    inventory_cover = Decimal(999)
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

    def run_in_transaction(self, data_date: date, connection) -> AnalysisSummary:
        """Rebuild one day using the caller's transaction boundary."""
        return self.run(data_date, _connection=connection)

    @staticmethod
    def _capabilities(rows) -> dict[str, str]:
        has_metrics = bool(rows)
        has_costs = has_metrics and all(
            not row["order_cost_coverage_incomplete"]
            and (
                row["product_cost"] is not None
                or row["order_product_cost_cny"] is not None
            )
            for row in rows
        )
        verified_fields = (
            "product_cost",
            "resolved_commission_rate",
            "seller_shipping",
            "coupons",
            "allocated_fixed_cost",
            "refund_amount",
            "rate_to_cny",
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
            and row["refund_data_status"] in {"observed", "t3_closed_zero"}
            and not row["order_cost_coverage_incomplete"]
            for row in rows
        )
        has_inventory = has_metrics and all(
            not row["inventory_required"]
            or (
                row["inventory_units"] is not None
                and row["expected_daily_units"] is not None
                and Decimal(row["expected_daily_units"]) > 0
            )
            for row in rows
        )
        inventory_not_applicable = has_metrics and all(
            not row["inventory_required"] for row in rows
        )
        return {
            "platform_metrics": "ready" if has_metrics else "pending_data",
            "estimated_profit": "ready" if has_costs else "pending_data",
            "verified_profit": (
                "ready" if has_verified_profit else "pending_data"
            ),
            "inventory_safe_strategy": (
                "not_applicable"
                if has_verified_profit and inventory_not_applicable
                else "ready"
                if has_verified_profit and has_inventory
                else "pending_data"
            ),
        }

    @staticmethod
    def _profit_input_lineage(row, data_date: date) -> tuple[InputLineage, ...]:
        day = data_date.isoformat()

        def age(source_date: str | None) -> int | None:
            if not source_date:
                return None
            delta = data_date - date.fromisoformat(source_date)
            return max(0, delta.days)

        def safe_ref(value: object | None) -> str:
            if value is None:
                return ""
            cleaned = "".join(
                char for char in str(value) if ord(char) >= 32 and ord(char) != 127
            )
            return cleaned.replace("\\", "/").rsplit("/", 1)[-1][:120]

        cost_date = row["cost_effective_date"]
        cost_ref = safe_ref(row["input_source_name"])
        items = [
            InputLineage(
                "gmv",
                row["attributed_gmv"],
                "ready",
                "daily_ad_metrics",
                day,
                0,
                detail_code="platform_attributed_gmv",
            ),
            InputLineage(
                "ad_spend",
                row["spend"],
                "ready",
                "daily_ad_metrics",
                day,
                0,
                detail_code="platform_ad_spend",
            ),
        ]

        refund_ready = (
            row["refund_amount"] is not None
            and row["refund_data_status"] in {"observed", "t3_closed_zero"}
        )
        if row["refund_source_type"]:
            refund_source_type = "daily_refund_inputs"
            refund_source_date = row["refund_source_date"] or day
            refund_source_ref = safe_ref(row["refund_source_ref"])
        else:
            refund_source_type = "product_costs"
            refund_source_date = cost_date
            refund_source_ref = cost_ref
        refund_detail = (
            "t3_no_refund_observed"
            if row["refund_data_status"] == "t3_closed_zero"
            else "refund_observed"
            if refund_ready
            else "refund_source_missing"
        )
        items.append(
            InputLineage(
                "refunds",
                row["refund_amount"] if refund_ready else None,
                "ready" if refund_ready else "pending_data",
                refund_source_type,
                refund_source_date if refund_ready else None,
                age(refund_source_date) if refund_ready else None,
                refund_source_ref
                if row["refund_source_type"]
                else cost_ref
                if refund_ready
                else "",
                refund_detail,
            )
        )

        fee_rate = row["resolved_commission_rate"]
        fee_selection = row["fee_selection"] or "fee_rate_missing"
        if row["fee_source_sha256"]:
            fee_source_type = "platform_fee_statements"
            fee_source_date = row["fee_period_end"]
            fee_ref = safe_ref(row["fee_source_name"])
        else:
            fee_source_type = "product_costs"
            fee_source_date = cost_date
            fee_ref = cost_ref
        fee_status = (
            "pending_data"
            if fee_rate is None
            else "carried_forward"
            if fee_selection in {"carried_forward", "legacy_cost_rate"}
            else "ready"
        )
        items.append(
            InputLineage(
                "commission_rate",
                fee_rate,
                fee_status,
                fee_source_type,
                fee_source_date if fee_rate is not None else None,
                age(fee_source_date) if fee_rate is not None else None,
                fee_ref if fee_rate is not None else "",
                fee_selection,
            )
        )

        if row["order_cost_allocation_ambiguous"]:
            product_cost = InputLineage(
                "product_cost",
                None,
                "ambiguous",
                row["cost_source_type"] or "order_costs",
                row["cost_source_date"],
                age(row["cost_source_date"]),
                detail_code="multiple_metric_allocation",
            )
        elif row["order_product_cost_cny"] is not None:
            product_cost = InputLineage(
                "product_cost",
                row["order_product_cost_cny"],
                "ready",
                row["cost_source_type"] or "order_cost_snapshots",
                row["cost_source_date"] or day,
                age(row["cost_source_date"] or day),
                detail_code="resolved_product_cost_cny",
            )
        elif row["order_cost_coverage_incomplete"]:
            product_cost = InputLineage(
                "product_cost",
                None,
                "pending_data",
                "order_cost_snapshots",
                day,
                0,
                detail_code="order_cost_coverage_incomplete",
            )
        elif row["product_cost"] is not None:
            product_cost = InputLineage(
                "product_cost",
                row["product_cost"],
                "ready",
                "product_costs",
                cost_date,
                age(cost_date),
                cost_ref,
                "product_cost_original_currency",
            )
        else:
            product_cost = InputLineage(
                "product_cost",
                None,
                "pending_data",
                "product_costs",
                None,
                None,
                detail_code="product_cost_missing",
            )
        items.append(product_cost)

        for input_name, column in (
            ("seller_shipping", "seller_shipping"),
            ("coupons", "coupons"),
            ("allocated_fixed_cost", "allocated_fixed_cost"),
        ):
            value = row[column]
            items.append(
                InputLineage(
                    input_name,
                    value,
                    "ready" if value is not None else "pending_data",
                    "product_costs",
                    cost_date if value is not None else None,
                    age(cost_date) if value is not None else None,
                    cost_ref if value is not None else "",
                    f"{input_name}_observed" if value is not None else f"{input_name}_missing",
                )
            )

        exchange_rate = row["rate_to_cny"]
        exchange_source_date = row["exchange_source_date"] or (
            day if exchange_rate is not None else None
        )
        exchange_detail = row["exchange_resolution_status"] or (
            "exact_date" if exchange_rate is not None else "exchange_rate_missing"
        )
        exchange_status = (
            "pending_data"
            if exchange_rate is None
            else "carried_forward"
            if exchange_source_date != day or "fallback" in exchange_detail
            else "ready"
        )
        items.append(
            InputLineage(
                "exchange_rate",
                exchange_rate,
                exchange_status,
                "exchange_rates",
                exchange_source_date,
                age(exchange_source_date),
                safe_ref(exchange_detail) if exchange_rate is not None else "",
                exchange_detail,
            )
        )
        return tuple(items)

    @staticmethod
    def _insert_lineage(connection, row, items: tuple[InputLineage, ...]) -> None:
        for item in items:
            connection.execute(
                """
                INSERT INTO profit_input_lineage(
                    platform, store, account_id, campaign_id, sku_id,
                    data_date, input_name, value_text, status, source_type,
                    source_date, age_days, source_ref, detail_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["platform"],
                    row["store"],
                    row["account_id"],
                    row["campaign_id"],
                    row["sku_id"],
                    row["data_date"],
                    item.input_name,
                    item.value_text,
                    item.status,
                    item.source_type,
                    item.source_date,
                    item.age_days,
                    item.source_ref,
                    item.detail_code,
                ),
            )

    @staticmethod
    def _upsert_profit(connection, row, profit) -> None:
        connection.execute(
            """
            INSERT INTO profit_results(
                platform, store, account_id, campaign_id, sku_id, data_date,
                net_sales_cny, platform_commission_cny, gross_profit_cny,
                net_profit_cny, break_even_roas, fee_effective_rate,
                fee_period_start, fee_period_end, fee_selection,
                fee_source_name, fee_source_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(
                platform, store, account_id, campaign_id, sku_id, data_date
            ) DO UPDATE SET
                net_sales_cny=excluded.net_sales_cny,
                platform_commission_cny=excluded.platform_commission_cny,
                gross_profit_cny=excluded.gross_profit_cny,
                net_profit_cny=excluded.net_profit_cny,
                break_even_roas=excluded.break_even_roas,
                fee_effective_rate=excluded.fee_effective_rate,
                fee_period_start=excluded.fee_period_start,
                fee_period_end=excluded.fee_period_end,
                fee_selection=excluded.fee_selection,
                fee_source_name=excluded.fee_source_name,
                fee_source_sha256=excluded.fee_source_sha256,
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
                row["resolved_commission_rate"],
                row["fee_period_start"],
                row["fee_period_end"],
                row["fee_selection"],
                row["fee_source_name"],
                row["fee_source_sha256"],
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
