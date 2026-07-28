from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from adwatch.ledger.models import ExpenseDraft
from adwatch.ledger.service import LedgerError, LedgerService


@dataclass(frozen=True)
class RouteResponse:
    status: int
    location: str | None = None
    message: str = ""


class DashboardRouter:
    def __init__(self, ledger: LedgerService, *, csrf_token: str) -> None:
        self.ledger = ledger
        self.csrf_token = csrf_token

    def post(self, path: str, form: dict[str, str]) -> RouteResponse:
        if form.get("csrf_token") != self.csrf_token:
            return RouteResponse(403, message="invalid CSRF token")
        try:
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
