from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from adwatch.inventory.models import PurchaseLine
from adwatch.inventory.service import InventoryService
from adwatch.ledger.models import ExpenseDraft
from adwatch.ledger.service import LedgerError, LedgerService
from adwatch.orders.fulfillment import FulfillmentService
from adwatch.orders.repository import OrderRepository
from adwatch.profit_sharing.service import ProfitSharingService


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
        inventory: InventoryService | None = None,
        orders: OrderRepository | None = None,
        fulfillment: FulfillmentService | None = None,
        profit_sharing: ProfitSharingService | None = None,
    ) -> None:
        self.ledger = ledger
        self.csrf_token = csrf_token
        self.inventory = inventory
        self.orders = orders
        self.fulfillment = fulfillment
        self.profit_sharing = profit_sharing

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
            occurred_on = date.fromisoformat(form["occurred_on"])
            if path == "/capital":
                self.ledger.create_capital(
                    partner=form["partner"],
                    entry_type=form["entry_type"],
                    amount=Decimal(form["amount"]),
                    occurred_on=occurred_on,
                    actor="local-web",
                )
                return RouteResponse(303, location="/operations")
            if path == "/withdrawals":
                self.ledger.create_withdrawal(
                    partner=form["partner"],
                    amount=Decimal(form["amount"]),
                    occurred_on=occurred_on,
                    purpose=form["purpose"],
                    actor="local-web",
                )
                return RouteResponse(303, location="/operations")
            if path == "/ad-funding":
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
                return RouteResponse(303, location="/operations")
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
        ) as error:
            return RouteResponse(400, message=str(error))
        return RouteResponse(303, location="/operations")
