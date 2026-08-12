from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from adwatch.accounting.exchange_rates import AccountingExchangeRateService
from adwatch.analytics.recalculation import AnalysisRecalculationService
from adwatch.approval.service import ApprovalError, ApprovalService
from adwatch.inventory.models import PurchaseLine
from adwatch.inventory.service import InventoryError, InventoryService
from adwatch.ledger.models import (
    AdFundingDraft,
    CapitalDraft,
    ExpenseDraft,
    ManualIncomeDraft,
    ReviewCostDraft,
)
from adwatch.ledger.service import LedgerError, LedgerService
from adwatch.orders.fulfillment import FulfillmentService
from adwatch.orders.repository import OrderRepository
from adwatch.profit_sharing.service import (
    ProfitSharingError,
    ProfitSharingService,
)
from adwatch.reconciliation.balances import (
    BalanceReconciliationError,
    BalanceReconciliationService,
)
from adwatch.storage.db import Database
from adwatch.strategy.service import StrategyRuleError, StrategyRuleService

RECALCULATION_INPUT_LABELS = {
    "allocated_fixed_cost": "分摊固定费用",
    "commission_rate": "平台佣金率",
    "coupons": "优惠券",
    "exchange_rate": "汇率",
    "product_cost": "商品单价",
    "order_cost_coverage": "订单成本覆盖",
    "refunds": "退款金额",
    "seller_shipping": "卖家承担运费",
}


@dataclass(frozen=True)
class RouteResponse:
    status: int
    location: str | None = None
    message: str = ""


class DashboardRouter:
    def __init__(
        self,
        ledger: LedgerService,
        *,
        csrf_token: str,
        database: Database | None = None,
        inventory: InventoryService | None = None,
        orders: OrderRepository | None = None,
        fulfillment: FulfillmentService | None = None,
        profit_sharing: ProfitSharingService | None = None,
        balances: BalanceReconciliationService | None = None,
        strategy_rule: StrategyRuleService | None = None,
        approval: ApprovalService | None = None,
    ) -> None:
        self.ledger = ledger
        self.csrf_token = csrf_token
        self.database = database
        self.inventory = inventory
        self.orders = orders
        self.fulfillment = fulfillment
        self.profit_sharing = profit_sharing
        self.balances = balances
        self.strategy_rule = strategy_rule
        self.approval = approval

    def _accounting_rate(self, form: dict[str, str], occurred_on: date):
        explicit = form.get("rate_to_cny", "").strip()
        if explicit:
            if not form.get("rate_reason", "").strip():
                raise ValueError("rate_reason is required for a line rate override")
            return Decimal(explicit), occurred_on, "manual_line_override"
        if self.database is None:
            raise ValueError("database is required to resolve exchange rates")
        resolved = AccountingExchangeRateService(self.database).resolve(
            form["currency"], occurred_on
        )
        return resolved.rate, resolved.source_date, resolved.source

    def post(self, path: str, form: dict[str, str]) -> RouteResponse:
        if form.get("csrf_token") != self.csrf_token:
            return RouteResponse(403, message="invalid CSRF token")
        try:
            if path == "/fulfillment" and self.fulfillment:
                self.fulfillment.set_policy(
                    platform=form["platform"],
                    store=form["store"],
                    seller_sku=form["seller_sku"],
                    effective_date=date.fromisoformat(form["effective_date"]),
                    mode=form["mode"],
                    supply_status=form["supply_status"],
                    note=form.get("note", ""),
                )
                return RouteResponse(303, location="/inventory")
            if path == "/sku-costs" and self.orders:
                self.orders.set_sku_cost(
                    platform=form["platform"],
                    store=form["store"],
                    seller_sku=form["seller_sku"],
                    effective_date=date.fromisoformat(form["effective_date"]),
                    unit_cost_cny=Decimal(form["unit_cost_cny"]),
                    note=form.get("note", ""),
                )
                return RouteResponse(303, location="/inventory")
            if path == "/purchases" and self.inventory:
                self.inventory.receive_purchase(
                    receipt_id=form["receipt_id"],
                    supplier=form["supplier"],
                    received_on=date.fromisoformat(form["received_on"]),
                    lines=(
                        PurchaseLine(
                            form["seller_sku"],
                            int(form["quantity"]),
                            Decimal(form["unit_cost_cny"]),
                        ),
                    ),
                    actor="local-web",
                )
                return RouteResponse(303, location="/inventory")
            if path == "/inventory/adjust" and self.inventory:
                self.inventory.adjust_inventory(
                    seller_sku=form["seller_sku"],
                    quantity_delta=int(form["quantity_delta"]),
                    occurred_on=date.fromisoformat(form["occurred_on"]),
                    reason=form["reason"],
                    actor="local-web",
                )
                return RouteResponse(303, location="/inventory")
            if path == "/inventory/movements/reverse" and self.inventory:
                self.inventory.reverse_movement(
                    form["movement_id"],
                    occurred_on=date.fromisoformat(form["occurred_on"]),
                    reason=form["reason"],
                    actor="local-web",
                )
                return RouteResponse(303, location="/inventory")
            if path == "/purchases/reverse" and self.inventory:
                self.inventory.reverse_purchase(
                    form["receipt_id"],
                    occurred_on=date.fromisoformat(form["occurred_on"]),
                    reason=form["reason"],
                    actor="local-web",
                )
                return RouteResponse(303, location="/inventory")
            if path == "/inventory/recalculate" and self.database:
                with self.database.connect() as connection:
                    metric_range = connection.execute(
                        """
                        SELECT MIN(data_date) AS start_date,
                               MAX(data_date) AS end_date
                        FROM daily_ad_metrics
                        """
                    ).fetchone()
                if not metric_range["start_date"]:
                    message = "未找到广告数据，没有可重算的日期。"
                    return RouteResponse(
                        303,
                        location="/inventory?"
                        + urlencode(
                            {
                                "recalc_status": "warning",
                                "recalc_message": message,
                            }
                        ),
                        message=message,
                    )
                start = date.fromisoformat(metric_range["start_date"])
                end = date.fromisoformat(metric_range["end_date"])
                stamp = datetime.now().astimezone().strftime(
                    "%Y%m%dT%H%M%S%f"
                )
                result = AnalysisRecalculationService(self.database).run(
                    start=start,
                    end=end,
                    destination=(
                        self.database.path.parent
                        / "reports"
                        / f"profit-recalculation-web-{stamp}.json"
                    ),
                )
                missing = "、".join(
                    f"{RECALCULATION_INPUT_LABELS.get(name, name)} {count}"
                    for name, count in result.missing_inputs.items()
                )
                if result.pending_data:
                    status = "warning"
                    message = (
                        f"重算已执行：覆盖 {result.days_processed} 个日期、"
                        f"处理 {result.metrics_processed} 条指标；"
                        f"生成 {result.profit_results} 条利润结果，"
                        f"仍有 {result.pending_data} 条待补数据。"
                        f"缺失项：{missing}。"
                    )
                else:
                    status = "success"
                    message = (
                        f"重算成功：覆盖 {result.days_processed} 个日期、"
                        f"处理 {result.metrics_processed} 条指标，"
                        f"生成 {result.profit_results} 条利润结果。"
                    )
                return RouteResponse(
                    303,
                    location="/inventory?"
                    + urlencode(
                        {
                            "recalc_status": status,
                            "recalc_message": message,
                        }
                    ),
                    message=message,
                )
            if path == "/profit-agreements" and self.profit_sharing:
                self.profit_sharing.create_agreement(
                    effective_from=date.fromisoformat(form["effective_from"]),
                    shares={
                        "洁云": Decimal(form["jieyun_share"]),
                        "苏姐": Decimal(form["sujie_share"]),
                    },
                    actor="local-web",
                )
                return RouteResponse(303, location="/profit-sharing")
            if path == "/profit-periods" and self.profit_sharing:
                self.profit_sharing.create_period_from_ledger(
                    starts_on=date.fromisoformat(form["starts_on"]),
                    ends_on=date.fromisoformat(form["ends_on"]),
                    actor="local-web",
                )
                return RouteResponse(303, location="/profit-sharing")
            if path == "/profit-payments" and self.profit_sharing:
                self.profit_sharing.record_payment(
                    period_id=form["period_id"],
                    partner=form["partner"],
                    amount_cny=Decimal(form["amount_cny"]),
                    paid_on=date.fromisoformat(form["paid_on"]),
                    status=form["status"],
                    note=form.get("note", ""),
                    actor="local-web",
                )
                return RouteResponse(303, location="/profit-sharing")
            if path == "/profit-periods/confirm" and self.profit_sharing:
                self.profit_sharing.confirm_period(
                    form["period_id"], actor="local-web"
                )
                return RouteResponse(303, location="/profit-sharing")
            if path == "/profit-payments/reverse" and self.profit_sharing:
                self.profit_sharing.reverse_payment(
                    form["payment_id"],
                    reversed_on=date.fromisoformat(form["reversed_on"]),
                    reason=form["reason"],
                    actor="local-web",
                )
                return RouteResponse(303, location="/profit-sharing")
            if path == "/profit-periods/reverse" and self.profit_sharing:
                self.profit_sharing.reverse_period(
                    form["period_id"],
                    reason=form["reason"],
                    actor="local-web",
                )
                return RouteResponse(303, location="/profit-sharing")
            if path == "/balance-snapshots" and self.balances:
                self.balances.record_snapshot(
                    account_type=form["account_type"],
                    account_name=form["account_name"],
                    snapshot_date=date.fromisoformat(form["snapshot_date"]),
                    amount_cny=Decimal(form["amount_cny"]),
                    source=form["source"],
                    platform=form.get("platform", ""),
                    store=form.get("store", ""),
                    note=form.get("note", ""),
                    actor="local-web",
                )
                return RouteResponse(303, location="/ad-funds")
            if path == "/balance-reconciliations" and self.balances:
                self.balances.reconcile(
                    data_date=date.fromisoformat(form["data_date"]),
                    actor="local-web",
                )
                return RouteResponse(303, location="/ad-funds")
            if path == "/balance-reconciliations/resolve" and self.balances:
                self.balances.resolve(
                    form["reconciliation_id"],
                    adjustment_id=form["adjustment_id"],
                    actor="local-web",
                )
                return RouteResponse(303, location="/ad-funds")
            if path == "/expenses/confirm":
                self.ledger.confirm_expense(
                    form["entry_id"], actor="local-web"
                )
                return RouteResponse(303, location="/ad-funds")
            if path == "/expenses/reverse":
                self.ledger.reverse_expense(
                    form["entry_id"],
                    actor="local-web",
                    reason=form["reason"],
                )
                return RouteResponse(303, location="/ad-funds")
            reverse_routes = {
                "/capital/reverse": self.ledger.reverse_capital,
                "/withdrawals/reverse": self.ledger.reverse_withdrawal,
                "/ad-funding/reverse": self.ledger.reverse_ad_funding,
                "/review-costs/reverse": self.ledger.reverse_review_order_cost,
            }
            if path in reverse_routes:
                reverse_routes[path](
                    form["entry_id"],
                    actor="local-web",
                    reason=form["reason"],
                )
                return RouteResponse(303, location="/ad-funds")
            if path == "/ledger/reverse":
                target_type, entry_id = form["target"].split(":", 1)
                methods = {
                    "capital": self.ledger.reverse_capital,
                    "withdrawal": self.ledger.reverse_withdrawal,
                    "ad_funding": self.ledger.reverse_ad_funding,
                    "review_order_cost": self.ledger.reverse_review_order_cost,
                }
                method = methods.get(target_type)
                if method is None:
                    return RouteResponse(400, message="invalid ledger target")
                method(
                    entry_id,
                    actor="local-web",
                    reason=form["reason"],
                )
                return RouteResponse(303, location="/ad-funds")
            if path == "/strategy-rules/create" and self.strategy_rule:
                import json

                self.strategy_rule.create_version(
                    parameters=json.loads(form["parameters"]),
                    effective_from=date.fromisoformat(
                        form["effective_from"]
                    ),
                    actor=form["actor"],
                )
                return RouteResponse(303, location="/strategy-rules")
            if path == "/strategy-rules/close" and self.strategy_rule:
                self.strategy_rule.close_version(form["version_id"])
                return RouteResponse(303, location="/strategy-rules")
            if path == "/strategy-rules/shadow" and self.strategy_rule:
                import json as json_lib
                import urllib.parse

                parameters = json_lib.loads(form.get("parameters", "{}"))
                shadow_results = self.strategy_rule.run_shadow(parameters)
                encoded = urllib.parse.quote(
                    json_lib.dumps(shadow_results, default=str)
                )
                return RouteResponse(
                    303, location=f"/strategy-rules?shadow_results={encoded}"
                )
            if path == "/approvals/create" and self.approval:
                self.approval.create(
                    recommendation_id=int(form["recommendation_id"]),
                )
                return RouteResponse(303, location="/approvals")
            if path == "/approvals/decide" and self.approval:
                self.approval.decide(
                    approval_id=form["approval_id"],
                    token=form["token"],
                    approve=form["decision"] == "approve",
                    actor=form["actor"],
                    reason=form.get("reason", ""),
                )
                return RouteResponse(303, location="/approvals")
            occurred_on = date.fromisoformat(form["occurred_on"])
            if path == "/adjustments":
                self.ledger.create_adjustment(
                    adjustment_type=form["adjustment_type"],
                    amount_cny=Decimal(form["amount_cny"]),
                    occurred_on=occurred_on,
                    reason=form["reason"],
                    actor="local-web",
                )
                return RouteResponse(303, location="/ad-funds")
            if path == "/adjustments/reverse":
                self.ledger.reverse_adjustment(
                    form["entry_id"],
                    occurred_on=occurred_on,
                    actor="local-web",
                    reason=form["reason"],
                )
                return RouteResponse(303, location="/ad-funds")
            if path == "/capital":
                if "currency" in form or "amount_original" in form:
                    rate, rate_date, rate_source = self._accounting_rate(
                        form, occurred_on
                    )
                    self.ledger.create_capital_draft(
                        CapitalDraft(
                            partner=form["partner"],
                            entry_type=form["entry_type"],
                            entry_name=form.get("entry_name", ""),
                            amount_original=Decimal(form["amount_original"]),
                            currency=form["currency"],
                            rate_to_cny=rate,
                            occurred_on=occurred_on,
                            rate_date=rate_date,
                            rate_source=rate_source,
                            note=form.get("note", ""),
                        ),
                        actor="local-web",
                    )
                else:
                    self.ledger.create_capital(
                        partner=form["partner"],
                        entry_type=form["entry_type"],
                        amount=Decimal(form["amount"]),
                        occurred_on=occurred_on,
                        actor="local-web",
                    )
                return RouteResponse(303, location="/ad-funds")
            if path == "/withdrawals":
                self.ledger.create_withdrawal(
                    partner=form["partner"],
                    amount=Decimal(form["amount"]),
                    occurred_on=occurred_on,
                    purpose=form["purpose"],
                    actor="local-web",
                )
                return RouteResponse(303, location="/ad-funds")
            if path == "/ad-funding":
                if "currency" in form or "paid_amount_original" in form:
                    rate, rate_date, rate_source = self._accounting_rate(
                        form, occurred_on
                    )
                    self.ledger.create_ad_funding_draft(
                        AdFundingDraft(
                            platform=form["platform"],
                            store=form["store"],
                            entry_type=form["entry_type"],
                            paid_amount_original=Decimal(
                                form["paid_amount_original"]
                            ),
                            credited_amount_original=Decimal(
                                form["credited_amount_original"]
                            ),
                            currency=form["currency"],
                            rate_to_cny=rate,
                            occurred_on=occurred_on,
                            rate_date=rate_date,
                            rate_source=rate_source,
                            source=form["source"],
                        ),
                        actor="local-web",
                    )
                else:
                    self.ledger.create_ad_funding(
                        platform=form["platform"],
                        store=form["store"],
                        entry_type=form["entry_type"],
                        amount=Decimal(form["amount"]),
                        occurred_on=occurred_on,
                        source=form["source"],
                        actor="local-web",
                    )
                return RouteResponse(303, location="/ad-funds")
            if path == "/review-costs":
                if "currency" in form or "goods_cost_original" in form:
                    rate, rate_date, rate_source = self._accounting_rate(
                        form, occurred_on
                    )
                    self.ledger.create_review_order_cost_draft(
                        ReviewCostDraft(
                            platform=form["platform"],
                            store=form["store"],
                            order_id=form["order_id"],
                            seller_sku=form.get("seller_sku", ""),
                            goods_cost_original=Decimal(
                                form["goods_cost_original"]
                            ),
                            service_fee_original=Decimal(
                                form["service_fee_original"]
                            ),
                            currency=form["currency"],
                            rate_to_cny=rate,
                            occurred_on=occurred_on,
                            rate_date=rate_date,
                            rate_source=rate_source,
                        ),
                        actor="local-web",
                    )
                else:
                    self.ledger.create_review_order_cost(
                        platform=form["platform"],
                        store=form["store"],
                        order_id=form["order_id"],
                        seller_sku=form.get("seller_sku", ""),
                        goods_cost=Decimal(form["goods_cost"]),
                        service_fee=Decimal(form["service_fee"]),
                        occurred_on=occurred_on,
                        actor="local-web",
                    )
                return RouteResponse(303, location="/ad-funds")
            if path == "/manual-income":
                rate, rate_date, rate_source = self._accounting_rate(
                    form, occurred_on
                )
                self.ledger.create_manual_income(
                    ManualIncomeDraft(
                        platform=form.get("platform", ""),
                        store=form.get("store", ""),
                        order_id=form.get("order_id", ""),
                        occurred_on=occurred_on,
                        amount_original=Decimal(form["amount_original"]),
                        currency=form["currency"],
                        rate_to_cny=rate,
                        rate_date=rate_date,
                        rate_source=rate_source,
                        note=form.get("note", ""),
                    ),
                    actor="local-web",
                )
                return RouteResponse(303, location="/ad-funds")
            if path != "/expenses":
                return RouteResponse(404, message="unknown route")
            draft = ExpenseDraft(
                occurred_on=occurred_on,
                category=form["category"],
                amount_original=Decimal(form["amount"]),
                currency=form["currency"],
                rate_to_cny=Decimal(form["rate_to_cny"]),
                payer=form["payer"],
                fund_nature=form["fund_nature"],
                affects_profit=form.get("affects_profit") == "1",
                affects_capital=form.get("affects_capital") == "1",
                note=form.get("note", ""),
            )
            self.ledger.create_expense(draft, actor="local-web")
        except (
            KeyError,
            ValueError,
            InvalidOperation,
            LedgerError,
            InventoryError,
            ProfitSharingError,
            BalanceReconciliationError,
            ApprovalError,
            StrategyRuleError,
        ) as error:
            return RouteResponse(400, message=str(error))
        return RouteResponse(303, location="/ad-funds")
