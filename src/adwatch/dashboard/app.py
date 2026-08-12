from __future__ import annotations

import html
import json
import secrets
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from adwatch.accounting.exchange_rates import (
    AccountingExchangeRateService,
    ExchangeRateInputError,
)
from adwatch.accounting.operating_statement import OperatingStatementService
from adwatch.approval.service import ApprovalService
from adwatch.dashboard.knowledge import (
    render_knowledge_detail,
    render_knowledge_page,
)
from adwatch.dashboard.routes import DashboardRouter
from adwatch.dashboard.views import (
    render_ad_funds_page,
    render_approvals_page,
    render_inventory_page,
    render_module_page,
    render_navigation,
    render_operating_statement_page,
    render_operations_page,
    render_optimization_page,
    render_profit_sharing_page,
    render_strategy_rules_page,
)
from adwatch.evidence.service import EvidenceError, EvidenceService
from adwatch.inventory.service import InventoryService
from adwatch.ledger.service import LedgerService
from adwatch.orders.fulfillment import FulfillmentService
from adwatch.orders.repository import OrderRepository
from adwatch.profit_sharing.service import ProfitSharingService
from adwatch.reconciliation.balances import BalanceReconciliationService
from adwatch.reporting.read_model import DailySnapshot, ReportReadModel
from adwatch.storage.db import Database
from adwatch.strategy.service import StrategyRuleService

INPUT_LABELS = {
    "gmv": "广告归因销售额",
    "refunds": "退款金额",
    "commission_rate": "平台费率",
    "product_cost": "商品成本",
    "ad_spend": "广告花费",
    "seller_shipping": "卖家运费",
    "coupons": "优惠券",
    "allocated_fixed_cost": "分摊固定成本",
    "exchange_rate": "汇率",
}
STATUS_LABELS = {
    "ready": "已就绪",
    "carried_forward": "沿用已有数据",
    "partial": "部分就绪",
    "pending_data": "待补数据",
    "ambiguous": "口径不明确",
}
FEE_SELECTION_LABELS = {
    "covering_period": "周期覆盖",
    "carried_forward": "沿用最近周期",
    "legacy_cost_rate": "历史成本费率",
    "pending_data": "待补数据",
}


@dataclass(frozen=True)
class EvidenceMultipart:
    fields: dict[str, str]
    filename: str
    media_type: str
    content: bytes


def _parse_evidence_multipart(
    content_type: str, body: bytes
) -> EvidenceMultipart:
    if not content_type.lower().startswith("multipart/form-data;"):
        raise ValueError("multipart/form-data is required")
    message = BytesParser(policy=policy.default).parsebytes(
        (
            f"Content-Type: {content_type}\r\n"
            "MIME-Version: 1.0\r\n\r\n"
        ).encode("ascii")
        + body
    )
    if not message.is_multipart():
        raise ValueError("invalid multipart body")
    fields: dict[str, str] = {}
    filename = ""
    media_type = ""
    content: bytes | None = None
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        part_filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if part_filename is not None:
            if content is not None or name != "file":
                raise ValueError("exactly one evidence file is required")
            filename = part_filename
            media_type = part.get_content_type()
            content = payload
        else:
            if name in fields:
                raise ValueError("duplicate multipart field")
            fields[name] = payload.decode("utf-8")
    if content is None or not filename:
        raise ValueError("evidence file is required")
    return EvidenceMultipart(
        fields=fields,
        filename=filename,
        media_type=media_type,
        content=content,
    )


def _format_decimal(value: Decimal | None, places: int = 2) -> str:
    return "N/A" if value is None else f"{value:.{places}f}"


def _format_cny(value: Decimal | None) -> str:
    if value is None:
        return "待补数据"
    sign = "-" if value < 0 else ""
    return f"{sign}¥{abs(value):.2f}"


def _resolve_dashboard_date(
    database: Database,
    *,
    requested: str | None,
    fallback: date,
) -> date:
    """Use the newest collected date for an unfiltered dashboard request."""
    if requested is not None:
        return date.fromisoformat(requested)
    return ReportReadModel(database).latest_data_date() or fallback


def _render_source_cards(
    snapshot: DailySnapshot, *, platform: str, store: str
) -> str:
    sources = tuple(
        item
        for item in snapshot.profit_sources
        if (not platform or item.platform == platform)
        and (not store or item.store == store)
    )
    if not sources:
        return '<p class="empty-state">当前筛选范围暂无来源数据</p>'
    cards = []
    for item in sources:
        status = STATUS_LABELS.get(item.profit_status, item.profit_status)
        missing = (
            "、".join(
                INPUT_LABELS.get(name, name) for name in item.missing_inputs
            )
            if item.missing_inputs
            else "无"
        )
        cost_coverage = (
            f"{item.confirmed_cost_orders}/{item.platform_orders} 个订单"
            if item.platform_orders
            else "待补平台订单"
        )
        refund_coverage = (
            f"{item.refund_amount_observations}/"
            f"{item.refund_observations} 个观测"
            if item.refund_observations
            else "待补退款金额观测"
        )
        inventory_date = (
            item.inventory_snapshot_date.isoformat()
            if item.inventory_snapshot_date is not None
            else "待补数据"
        )
        exchange_date = (
            item.exchange_source_date.isoformat()
            if item.exchange_source_date is not None
            else "待补数据"
        )
        fee_period = (
            f"{item.fee_period_start.isoformat()} 至 "
            f"{item.fee_period_end.isoformat()}"
            if item.fee_period_start is not None
            and item.fee_period_end is not None
            else "待补数据"
        )
        lineage_items = "".join(
            f"""
            <li>
              <span>{html.escape(INPUT_LABELS.get(lineage.input_name, lineage.input_name))}</span>
              <strong>{html.escape(STATUS_LABELS.get(lineage.status, lineage.status))}</strong>
              <small>{html.escape(lineage.source_type)} · {lineage.source_date.isoformat() if lineage.source_date else '无来源日期'} · {lineage.covered_rows}/{lineage.total_rows} 行</small>
            </li>
            """
            for lineage in snapshot.profit_input_lineage
            if lineage.platform == item.platform and lineage.store == item.store
        ) or '<li><span>利润输入来源</span><strong>待补数据</strong></li>'
        cards.append(
            f"""
            <article class="data-source-card data-status-{html.escape(item.profit_status)}">
              <header class="source-card-header">
                <div>
                  <p>{html.escape(item.platform.title())}</p>
                  <h3>{html.escape(item.store)}</h3>
                </div>
                <span class="source-status">利润{html.escape(status)}</span>
              </header>
              <dl class="source-stat-grid">
                <div><dt>广告数据</dt><dd>{item.metric_rows} 行 · 归因 {item.attributed_orders} 单</dd></div>
                <div><dt>平台订单</dt><dd>{item.platform_orders} 单</dd></div>
                <div><dt>成本覆盖</dt><dd>{cost_coverage}</dd></div>
                <div><dt>退款金额</dt><dd>{refund_coverage}</dd></div>
                <div><dt>库存快照</dt><dd>{inventory_date}</dd></div>
                <div><dt>汇率来源</dt><dd>{exchange_date} · {html.escape(item.exchange_resolution_status)}</dd></div>
                <div><dt>结算费率</dt><dd>{fee_period} · {html.escape(FEE_SELECTION_LABELS.get(item.fee_selection, item.fee_selection))}</dd></div>
                <div class="source-gap"><dt>关键缺口</dt><dd>{html.escape(missing)}</dd></div>
              </dl>
              <details class="source-lineage">
                <summary>查看利润输入来源</summary>
                <ul>{lineage_items}</ul>
              </details>
            </article>
            """
        )
    return "".join(cards)


def render_dashboard(
    database: Database,
    data_date: date,
    *,
    simulated: bool,
    platform: str = "",
    store: str = "",
    campaign: str = "",
    sku: str = "",
) -> str:
    dashboard_snapshot = ReportReadModel(database).dashboard(data_date)
    statements = OperatingStatementService(database)
    current_month = statements.monthly(data_date.year, data_date.month)
    annual = statements.yearly(data_date.year)
    annual_revenue = sum(
        (item.revenue_cny for item in annual), Decimal(0)
    )
    annual_cost = sum(
        (item.cogs_cny or Decimal(0) for item in annual), Decimal(0)
    )
    annual_expense = sum(
        (item.operating_expense_cny for item in annual), Decimal(0)
    )
    annual_profit = (
        None
        if any(item.net_profit_cny is None for item in annual)
        else sum(
            (
                item.net_profit_cny
                for item in annual
                if item.net_profit_cny is not None
            ),
            Decimal(0),
        )
    )
    rate_service = AccountingExchangeRateService(database)
    rate_labels = []
    for currency in ("THB", "SGD"):
        try:
            rate = rate_service.resolve(currency, data_date).rate
            rate_labels.append(f"{currency} → CNY {rate}")
            if currency == "THB":
                rate_labels.append(f"CNY → THB {(Decimal(1) / rate):.4f}")
        except ExchangeRateInputError:
            rate_labels.append(f"{currency} → CNY 待补数据")
    operating_cards = f"""
    <section class="panel">
      <div class="panel-header"><h2>本月经营</h2><a href="/operating-statement?year={data_date.year}">查看月度经营账</a></div>
      <p>收入 {_format_cny(current_month.revenue_cny)} · 销售成本 {_format_cny(current_month.cogs_cny)} · 广告实际消耗 {_format_cny(current_month.ad_spend_cny)} · 经营费用 {_format_cny(current_month.operating_expense_cny)} · 净利润 {_format_cny(current_month.net_profit_cny)}</p>
      <p>{' · '.join(rate_labels)}</p>
    </section>
    <section class="panel">
      <div class="panel-header"><h2>年度经营</h2></div>
      <p>收入 {_format_cny(annual_revenue)} · 销售成本 {_format_cny(annual_cost)} · 经营费用 {_format_cny(annual_expense)} · 净利润 {_format_cny(annual_profit)}</p>
    </section>
    """
    snapshot = dashboard_snapshot.daily
    platform_items = tuple(
        item
        for item in snapshot.platforms
        if not platform or item.platform == platform
    )
    sku_items = tuple(
        item
        for item in snapshot.sku_performance
        if (not platform or item.platform == platform)
        and (not store or item.store == store)
        and (not campaign or item.campaign_id == campaign)
        and (not sku or item.sku_id == sku)
    )
    cards = "".join(
        f"""
        <article class="kpi-card">
          <div class="eyebrow">{html.escape(item.platform.title())}</div>
          <div class="roas-value">
            <span class="roas-number{' roas-accent' if item.roas and item.roas > 1 else ' roas-danger' if item.roas and item.roas < 1 else ''}">{_format_decimal(item.roas)}</span>
            <span class="roas-label">ROAS</span>
          </div>
          <dl class="kpi-details">
            <div class="kpi-detail"><dt>消耗</dt><dd class="number">{_format_decimal(item.spend)}</dd></div>
            <div class="kpi-detail"><dt>GMV</dt><dd class="number">{_format_decimal(item.gmv)}</dd></div>
            <div class="kpi-detail"><dt>订单</dt><dd class="number">{item.orders}</dd></div>
            <div class="kpi-detail"><dt>净利润</dt><dd class="number{' profit-positive' if item.net_profit and item.net_profit > 0 else ' profit-negative' if item.net_profit and item.net_profit < 0 else ''}">{_format_cny(item.net_profit)}</dd></div>
          </dl>
        </article>
        """
        for item in platform_items
    )
    rows = "".join(
        f"""
        <tr>
          <td>{html.escape(item.platform.title())}</td>
          <td>{html.escape(item.store)}</td>
          <td>{html.escape(item.campaign_id)}</td>
          <td>{html.escape(item.sku_id)}</td>
          <td class="number text-roas">{_format_decimal(item.roas)}</td>
          <td class="number{' text-profit-positive' if item.net_profit and item.net_profit > 0 else ' text-profit-negative' if item.net_profit and item.net_profit < 0 else ''}">{_format_cny(item.net_profit)}</td>
        </tr>
        """
        for item in sku_items
    )
    alerts = "".join(
        f'<li><strong class="severity-{html.escape(str(item["severity"]).lower())}">{html.escape(item["severity"])}</strong> '
        f"<span>{html.escape(item['message'])}</span></li>"
        for item in snapshot.alerts
    ) or '<li style="justify-content:center;color:var(--color-text-muted);background:none;">当前无异常告警</li>'
    recommendations = "".join(
        f'<li><strong class="severity-info">{html.escape(item["action"])}</strong> '
        f"<span>{html.escape(item['platform'])}/{html.escape(item['campaign_id'])} — "
        f"{html.escape(item['reason'])}</span></li>"
        for item in snapshot.recommendations
    ) or '<li style="justify-content:center;color:var(--color-text-muted);background:none;">当前无策略建议</li>'
    source_cards = _render_source_cards(
        snapshot, platform=platform, store=store
    )
    source_badge = "模拟数据" if simulated else "真实数据"
    scope_label = platform.title() if platform else "TikTok + Shopee"
    trends = "".join(
        "<section class=\"panel\">"
        f'<div class="panel-header"><h2>{days} 天趋势</h2></div>'
        "<div class=\"table-wrap\"><table>"
        "<thead><tr><th>日期</th><th>消耗</th><th>GMV</th><th>ROAS</th>"
        "<th>净利润</th></tr></thead><tbody>"
        + (
            "".join(
                "<tr>"
                f"<td>{point.data_date.isoformat()}</td>"
                f"<td class=\"number\">{_format_decimal(point.spend)}</td>"
                f"<td class=\"number\">{_format_decimal(point.gmv)}</td>"
                f"<td class=\"number text-roas\">{_format_decimal(point.roas)}</td>"
                f"<td class=\"number{' text-profit-positive' if point.net_profit and point.net_profit > 0 else ' text-profit-negative' if point.net_profit and point.net_profit < 0 else ''}\">{_format_cny(point.net_profit)}</td>"
                "</tr>"
                for point in points
            )
            or '<tr><td colspan="5" class="empty-state">暂无数据</td></tr>'
        )
        + "</tbody></table></div></section>"
        for days, points in dashboard_snapshot.trends.items()
    )
    run_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(run['platform']))}</td>"
        f"<td>{html.escape(str(run['status']))}</td>"
        f"<td>{run['accepted_count']}</td>"
        f"<td>{run['quarantined_count']}</td>"
        "</tr>"
        for run in dashboard_snapshot.collection_runs
    ) or '<tr><td colspan="4">暂无运行记录</td></tr>'
    approval_summary = ", ".join(
        f"{html.escape(status)}: {count}"
        for status, count in sorted(
            dashboard_snapshot.approval_counts.items()
        )
    ) or "暂无审批"
    execution_summary = ", ".join(
        f"{html.escape(status)}: {count}"
        for status, count in sorted(
            dashboard_snapshot.execution_counts.items()
        )
    ) or "暂无执行"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AdWatch 投放驾驶舱</title>
  <style>
    /* ===== Design Tokens ===== */
    :root {{
      /* 主色系 */
      --color-primary: #4F46E5;
      --color-primary-light: #6366F1;
      --color-primary-dark: #3730A3;
      --color-primary-bg: #EEF2FF;

      /* 强调色 */
      --color-accent: #F59E0B;
      --color-accent-light: #FBBF24;
      --color-accent-bg: #FFFBEB;

      /* 功能色 */
      --color-success: #10B981;
      --color-success-bg: #ECFDF5;
      --color-warning: #F59E0B;
      --color-warning-bg: #FFFBEB;
      --color-danger: #EF4444;
      --color-danger-bg: #FEF2F2;

      /* 中性色 */
      --color-text: #0F172A;
      --color-text-secondary: #475569;
      --color-text-muted: #94A3B8;
      --color-text-inverse: #FFFFFF;

      /* 背景色 */
      --color-bg: #F8FAFC;
      --color-bg-gradient: linear-gradient(135deg, #F8FAFC 0%, #EEF2FF 100%);
      --color-surface: #FFFFFF;
      --color-surface-hover: #F8FAFC;

      /* 边框 */
      --color-border: #E2E8F0;
      --color-border-light: #F1F5F9;

      /* 阴影 */
      --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.05);
      --shadow-md: 0 4px 12px rgba(15, 23, 42, 0.06);
      --shadow-lg: 0 8px 24px rgba(15, 23, 42, 0.08);
      --shadow-xl: 0 20px 40px rgba(15, 23, 42, 0.1);
      --shadow-card-hover: 0 12px 32px rgba(79, 70, 229, 0.12);

      /* 圆角 */
      --radius-sm: 6px;
      --radius-md: 10px;
      --radius-lg: 14px;
      --radius-xl: 20px;

      /* 间距 */
      --space-xs: 4px;
      --space-sm: 8px;
      --space-md: 16px;
      --space-lg: 24px;
      --space-xl: 32px;

      /* 过渡 */
      --transition-fast: 150ms ease;
      --transition-normal: 250ms ease;

      /* 字体 */
      --font-sans: "Inter", "Noto Sans SC", -apple-system, BlinkMacSystemFont,
        "PingFang SC", "Microsoft YaHei", "Segoe UI", Roboto, sans-serif;
      --font-mono: "JetBrains Mono", "SF Mono", "Fira Code", ui-monospace, monospace;

      /* 布局 */
      --container-max: 1200px;
    }}

    /* ===== Reset ===== */
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    /* ===== Base ===== */
    html {{ scroll-behavior: smooth; -webkit-font-smoothing: antialiased; }}
    body {{
      color: var(--color-text);
      background: var(--color-bg);
      background-image: var(--color-bg-gradient);
      background-attachment: fixed;
      font-family: var(--font-sans);
      font-size: 15px;
      line-height: 1.6;
      min-height: 100vh;
    }}

    /* ===== Skip Link ===== */
    .skip {{ position: absolute; left: -9999px; top: auto; }}
    .skip:focus {{
      left: 16px; top: 16px; z-index: 100;
      padding: 12px 20px;
      background: var(--color-primary);
      color: var(--color-text-inverse);
      border-radius: var(--radius-md);
      outline: 3px solid var(--color-accent);
      font-weight: 600;
      text-decoration: none;
    }}

    /* ===== Container ===== */
    header, main {{
      width: min(var(--container-max), calc(100% - 32px));
      margin: 0 auto;
    }}

    /* ===== Header ===== */
    .page-header {{
      padding: 32px 0 24px;
      display: flex;
      gap: var(--space-md);
      justify-content: space-between;
      align-items: flex-end;
      flex-wrap: wrap;
    }}
    .page-header h1 {{
      margin: 0;
      font-size: clamp(1.75rem, 4vw, 2.5rem);
      font-weight: 800;
      letter-spacing: -0.03em;
      background: linear-gradient(135deg, var(--color-primary) 0%, var(--color-primary-light) 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }}
    .header-meta {{ margin: 6px 0 0; color: var(--color-text-muted); font-size: 0.9rem; }}
    .header-actions {{ display: flex; align-items: center; gap: var(--space-sm); }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: var(--color-primary-bg);
      color: var(--color-primary);
      padding: 6px 14px;
      border-radius: 999px;
      font-weight: 600;
      font-size: 0.8rem;
      white-space: nowrap;
      border: 1px solid rgba(79, 70, 229, 0.2);
    }}
    .badge::before {{
      content: "";
      width: 6px; height: 6px;
      border-radius: 50%;
      background: var(--color-primary);
    }}
    .badge.badge-simulated {{
      background: var(--color-warning-bg);
      color: #B45309;
      border-color: rgba(245, 158, 11, 0.3);
    }}
    .badge.badge-simulated::before {{ background: var(--color-warning); }}

    /* ===== Navigation ===== */
    .nav-bar {{
      display: flex;
      gap: 4px;
      padding: 6px;
      background: var(--color-surface);
      border: 1px solid var(--color-border);
      border-radius: var(--radius-lg);
      box-shadow: var(--shadow-sm);
      margin-bottom: var(--space-lg);
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
    }}
    .nav-bar a {{
      position: relative;
      padding: 10px 18px;
      border-radius: var(--radius-md);
      color: var(--color-text-secondary);
      font-weight: 500;
      font-size: 0.88rem;
      text-decoration: none;
      white-space: nowrap;
      transition: color var(--transition-fast), background-color var(--transition-fast);
    }}
    .nav-bar a:hover {{
      color: var(--color-primary);
      background: var(--color-primary-bg);
    }}
    .nav-bar a[aria-current="page"] {{
      color: var(--color-text-inverse);
      background: var(--color-primary);
      box-shadow: 0 2px 8px rgba(79, 70, 229, 0.3);
    }}
    .nav-bar a[aria-current="page"]::after {{
      content: "";
      position: absolute;
      bottom: -6px;
      left: 50%;
      transform: translateX(-50%);
      width: 20px;
      height: 3px;
      background: var(--color-accent);
      border-radius: 2px;
    }}

    /* ===== Filter Form ===== */
    .filter-form {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: var(--space-md);
      align-items: end;
    }}
    .filter-form label {{
      display: flex;
      flex-direction: column;
      gap: 6px;
      font-size: 0.82rem;
      font-weight: 500;
      color: var(--color-text-secondary);
    }}
    .filter-form input,
    .filter-form select {{
      width: 100%;
      padding: 10px 14px;
      border: 1px solid var(--color-border);
      border-radius: var(--radius-md);
      background: var(--color-surface);
      color: var(--color-text);
      font-family: var(--font-sans);
      font-size: 0.9rem;
      transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
    }}
    .filter-form input:focus,
    .filter-form select:focus {{
      outline: none;
      border-color: var(--color-primary);
      box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.15);
    }}
    .filter-form button {{
      padding: 10px 24px;
      background: var(--color-primary);
      color: var(--color-text-inverse);
      border: none;
      border-radius: var(--radius-md);
      font-weight: 600;
      font-size: 0.9rem;
      cursor: pointer;
      transition: background-color var(--transition-fast), transform var(--transition-fast);
    }}
    .filter-form button:hover {{
      background: var(--color-primary-dark);
      transform: translateY(-1px);
      box-shadow: 0 4px 12px rgba(79, 70, 229, 0.3);
    }}

    /* ===== Cards Grid (KPI Cards) ===== */
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: var(--space-lg);
      margin-top: var(--space-lg);
    }}
    .kpi-card {{
      position: relative;
      background: var(--color-surface);
      border: 1px solid var(--color-border);
      border-radius: var(--radius-xl);
      padding: var(--space-lg);
      box-shadow: var(--shadow-md);
      transition: transform var(--transition-normal), box-shadow var(--transition-normal);
      overflow: hidden;
    }}
    .kpi-card::before {{
      content: "";
      position: absolute;
      top: 0; left: 0; right: 0;
      height: 4px;
      background: linear-gradient(90deg, var(--color-primary), var(--color-primary-light));
    }}
    .kpi-card:hover {{
      transform: translateY(-4px);
      box-shadow: var(--shadow-card-hover);
    }}
    .kpi-card .eyebrow {{
      display: inline-flex;
      align-items: center;
      padding: 4px 12px;
      background: var(--color-primary-bg);
      color: var(--color-primary);
      font-size: 0.75rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      border-radius: 999px;
      margin-bottom: var(--space-md);
    }}
    .kpi-card .roas-value {{
      display: flex;
      align-items: baseline;
      gap: 8px;
      margin-bottom: var(--space-lg);
    }}
    .kpi-card .roas-number {{
      font-size: 2.8rem;
      font-weight: 800;
      letter-spacing: -0.04em;
      color: var(--color-primary);
      font-variant-numeric: tabular-nums;
      line-height: 1;
    }}
    .kpi-card .roas-label {{
      font-size: 0.9rem;
      font-weight: 600;
      color: var(--color-text-muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .kpi-card .roas-accent {{
      color: var(--color-accent);
    }}
    .kpi-card .roas-success {{
      color: var(--color-success);
    }}
    .kpi-card .roas-danger {{
      color: var(--color-danger);
    }}

    /* KPI detail grid */
    .kpi-details {{
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 12px;
    }}
    .kpi-detail {{
      padding: 12px;
      background: var(--color-bg);
      border-radius: var(--radius-md);
      border: 1px solid var(--color-border-light);
      transition: background-color var(--transition-fast);
    }}
    .kpi-detail:hover {{
      background: var(--color-primary-bg);
    }}
    .kpi-detail dt {{
      color: var(--color-text-muted);
      font-size: 0.78rem;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin-bottom: 4px;
    }}
    .kpi-detail dd {{
      margin: 0;
      font-size: 1.05rem;
      font-weight: 700;
      color: var(--color-text);
      font-variant-numeric: tabular-nums;
    }}
    .kpi-detail dd.profit-positive {{ color: var(--color-success); }}
    .kpi-detail dd.profit-negative {{ color: var(--color-danger); }}

    /* ===== Panels (Sections) ===== */
    .panel {{
      background: var(--color-surface);
      border: 1px solid var(--color-border);
      border-radius: var(--radius-xl);
      padding: var(--space-lg);
      margin-top: var(--space-lg);
      box-shadow: var(--shadow-sm);
    }}
    .panel-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: var(--space-lg);
    }}
    .panel-header h2 {{
      font-size: 1.1rem;
      font-weight: 700;
      color: var(--color-text);
      margin: 0;
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .panel-header h2::before {{
      content: "";
      width: 4px;
      height: 20px;
      background: linear-gradient(180deg, var(--color-primary), var(--color-primary-light));
      border-radius: 2px;
    }}
    .panel-header .panel-desc {{
      color: var(--color-text-muted);
      font-size: 0.85rem;
      font-weight: normal;
    }}

    /* ===== Data Source Trust Cards ===== */
    .data-source-list {{
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: var(--space-md);
    }}
    .data-source-card {{
      min-width: 0;
      padding: var(--space-lg);
      border: 1px solid var(--color-border);
      border-left: 4px solid var(--color-warning);
      border-radius: var(--radius-lg);
      background: var(--color-bg);
      overflow-wrap: anywhere;
    }}
    .data-source-card.data-status-ready {{ border-left-color: var(--color-success); }}
    .data-source-card.data-status-partial {{ border-left-color: var(--color-warning); }}
    .source-card-header {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: var(--space-md);
      margin-bottom: var(--space-md);
    }}
    .source-card-header p {{
      color: var(--color-text-muted);
      font-size: 0.75rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }}
    .source-card-header h3 {{ font-size: 1rem; line-height: 1.4; }}
    .source-status {{
      flex: 0 0 auto;
      padding: 4px 10px;
      border: 1px solid var(--color-border);
      border-radius: 999px;
      background: var(--color-surface);
      color: var(--color-text-secondary);
      font-size: 0.76rem;
      font-weight: 700;
    }}
    .source-stat-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: var(--space-sm);
    }}
    .source-stat-grid > div {{
      min-width: 0;
      padding: 10px 12px;
      border: 1px solid var(--color-border-light);
      border-radius: var(--radius-md);
      background: var(--color-surface);
    }}
    .source-stat-grid dt {{ color: var(--color-text-muted); font-size: 0.76rem; }}
    .source-stat-grid dd {{
      margin-top: 3px;
      color: var(--color-text);
      font-size: 0.88rem;
      font-weight: 650;
      font-variant-numeric: tabular-nums;
    }}
    .source-stat-grid .source-gap {{ grid-column: 1 / -1; }}
    .source-lineage {{
      margin-top: var(--space-md);
      border-top: 1px solid var(--color-border);
      padding-top: var(--space-sm);
    }}
    .source-lineage summary {{
      min-height: 44px;
      display: flex;
      align-items: center;
      color: var(--color-primary);
      cursor: pointer;
      font-weight: 650;
    }}
    .source-lineage summary:focus-visible {{
      outline: 3px solid rgba(79, 70, 229, 0.25);
      outline-offset: 2px;
    }}
    .source-lineage ul {{ list-style: none; display: grid; gap: 6px; }}
    .source-lineage li {{
      display: grid;
      grid-template-columns: minmax(120px, 0.7fr) minmax(100px, 0.5fr) minmax(0, 2fr);
      gap: var(--space-sm);
      align-items: baseline;
      padding: 8px 10px;
      border-radius: var(--radius-sm);
      background: var(--color-surface);
      font-size: 0.82rem;
    }}
    .source-lineage small {{ color: var(--color-text-muted); }}

    /* ===== Tables ===== */
    .table-wrap {{
      overflow-x: auto;
      border-radius: var(--radius-lg);
      border: 1px solid var(--color-border-light);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 600px;
      font-size: 0.88rem;
    }}
    thead {{
      background: var(--color-bg);
    }}
    th {{
      padding: 14px 16px;
      text-align: left;
      color: var(--color-text-muted);
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      white-space: nowrap;
      border-bottom: 2px solid var(--color-border);
    }}
    td {{
      padding: 12px 16px;
      border-bottom: 1px solid var(--color-border-light);
      color: var(--color-text);
    }}
    tbody tr {{
      transition: background-color var(--transition-fast);
    }}
    tbody tr:hover {{
      background: var(--color-primary-bg);
    }}
    tbody tr:last-child td {{ border-bottom: none; }}
    .number {{
      font-family: var(--font-mono);
      font-variant-numeric: tabular-nums;
      font-weight: 600;
    }}
    .text-profit-positive {{ color: var(--color-success); }}
    .text-profit-negative {{ color: var(--color-danger); }}
    .text-roas {{ color: var(--color-accent); font-weight: 700; }}

    /* ===== Lists (Alerts & Recommendations) ===== */
    .lists-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: var(--space-lg);
      margin-top: var(--space-lg);
      margin-bottom: var(--space-xl);
    }}
    .list-panel ul {{
      margin: 0;
      padding: 0;
      list-style: none;
    }}
    .list-panel li {{
      padding: 12px 16px;
      border-radius: var(--radius-md);
      background: var(--color-bg);
      margin-bottom: 8px;
      display: flex;
      gap: 10px;
      align-items: flex-start;
      transition: background-color var(--transition-fast);
    }}
    .list-panel li:hover {{
      background: var(--color-primary-bg);
    }}
    .list-panel li strong {{
      flex-shrink: 0;
      padding: 2px 8px;
      border-radius: 6px;
      font-size: 0.72rem;
      font-weight: 700;
      text-transform: uppercase;
    }}
    .list-panel li .severity-high {{
      background: var(--color-danger-bg);
      color: var(--color-danger);
    }}
    .list-panel li .severity-medium {{
      background: var(--color-warning-bg);
      color: var(--color-warning);
    }}
    .list-panel li .severity-low {{
      background: var(--color-success-bg);
      color: var(--color-success);
    }}
    .list-panel li .severity-info {{
      background: var(--color-primary-bg);
      color: var(--color-primary);
    }}
    .list-panel li span {{
      color: var(--color-text-secondary);
      font-size: 0.88rem;
      line-height: 1.5;
    }}

    /* ===== Status Summary ===== */
    .status-summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: var(--space-md);
    }}
    .status-chip {{
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 14px 18px;
      background: var(--color-bg);
      border-radius: var(--radius-lg);
      border: 1px solid var(--color-border-light);
    }}
    .status-chip .status-label {{
      font-size: 0.78rem;
      color: var(--color-text-muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      font-weight: 600;
    }}
    .status-chip .status-value {{
      font-size: 1.1rem;
      font-weight: 700;
      color: var(--color-text);
    }}

    /* ===== Empty State ===== */
    .empty-state {{
      text-align: center;
      padding: 32px 20px;
      color: var(--color-text-muted);
      font-size: 0.9rem;
    }}

    /* ===== Responsive ===== */
    @media (max-width: 900px) {{
      .kpi-grid {{ grid-template-columns: 1fr; }}
      .lists-grid {{ grid-template-columns: 1fr; }}
      .kpi-card .roas-number {{ font-size: 2.2rem; }}
    }}
    @media (max-width: 700px) {{
      .page-header {{
        flex-direction: column;
        align-items: flex-start;
        gap: var(--space-md);
      }}
      .nav-bar {{ padding: 4px; gap: 2px; }}
      .nav-bar a {{ padding: 8px 14px; font-size: 0.82rem; }}
      .filter-form {{ grid-template-columns: 1fr; }}
      .kpi-details {{ grid-template-columns: repeat(2, 1fr); }}
      .panel {{ padding: var(--space-md); }}
      th, td {{ padding: 10px 12px; font-size: 0.82rem; }}
      .source-stat-grid {{ grid-template-columns: 1fr; }}
      .source-lineage li {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 480px) {{
      .kpi-details {{ grid-template-columns: 1fr; }}
      .status-summary {{ grid-template-columns: 1fr; }}
    }}

    /* ===== Reduced Motion ===== */
    @media (prefers-reduced-motion: reduce) {{
      *, *::before, *::after {{
        animation-duration: 0.01ms !important;
        transition-duration: 0.01ms !important;
      }}
    }}
  </style>
</head>
<body>
  <a class="skip" href="#main">跳到主要内容</a>
  <header class="page-header">
    <div>
      <h1>AdWatch 投放驾驶舱</h1>
      <p class="header-meta">{data_date.isoformat()} · {scope_label}</p>
    </div>
    <div class="header-actions">
      <span class="badge{' badge-simulated' if simulated else ''}">{source_badge}</span>
    </div>
  </header>
  <main id="main">
    {render_navigation("/")}
    <form method="get" class="filter-form panel" aria-label="筛选">
      <label>日期
        <input name="date" type="date" value="{data_date.isoformat()}">
      </label>
      <label>平台
        <select name="platform">
          <option value="">全部</option>
          <option value="tiktok">TikTok</option>
          <option value="shopee">Shopee</option>
        </select>
      </label>
      <label>店铺
        <input name="store" value="{html.escape(store)}" placeholder="店铺名称">
      </label>
      <label>Campaign
        <input name="campaign" value="{html.escape(campaign)}" placeholder="Campaign ID">
      </label>
      <label>SKU
        <input name="sku" value="{html.escape(sku)}" placeholder="SKU ID">
      </label>
      <button type="submit">应用筛选</button>
    </form>
    <section class="kpi-grid" aria-label="平台核心指标">{cards}</section>
    {operating_cards}
    <section class="panel" aria-labelledby="source-trust-title">
      <div class="panel-header">
        <h2 id="source-trust-title">数据来源与可信度</h2>
        <span class="panel-desc">按店展示利润输入覆盖与来源新鲜度</span>
      </div>
      <div class="data-source-list">{source_cards}</div>
    </section>
    <section class="panel">
      <div class="panel-header"><h2>Campaign 与 SKU 表现</h2></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>平台</th><th>店铺</th>
          <th>Campaign</th><th>SKU</th><th>ROAS</th><th>净利润</th>
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </section>
    {trends}
    <section class="panel">
      <div class="panel-header"><h2>采集运行质量</h2></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>平台</th><th>状态</th>
          <th>写入</th><th>隔离</th></tr></thead>
          <tbody>{run_rows}</tbody>
        </table>
      </div>
    </section>
    <section class="panel">
      <div class="panel-header"><h2>审批与执行状态</h2></div>
      <div class="status-summary">
        <div class="status-chip">
          <div>
            <div class="status-label">审批状态</div>
            <div class="status-value">{approval_summary}</div>
          </div>
        </div>
        <div class="status-chip">
          <div>
            <div class="status-label">执行状态</div>
            <div class="status-value">{execution_summary}</div>
          </div>
        </div>
      </div>
    </section>
    <div class="lists-grid">
      <section class="panel list-panel">
        <div class="panel-header"><h2>异常告警</h2></div>
        <ul>{alerts}</ul>
      </section>
      <section class="panel list-panel">
        <div class="panel-header"><h2>策略建议</h2></div>
        <ul>{recommendations}</ul>
      </section>
    </div>
  </main>
</body></html>"""


def snapshot_json(snapshot: DailySnapshot) -> bytes:
    return json.dumps(
        asdict(snapshot),
        default=lambda value: (
            str(value) if isinstance(value, Decimal) else value.isoformat()
        ),
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")


def serve(
    database: Database,
    *,
    host: str,
    port: int,
    default_date: date,
    simulated: bool | None,
    evidence_root: Path | None = None,
) -> None:
    csrf_token = secrets.token_urlsafe(32)
    evidence = EvidenceService(
        database,
        evidence_root or database.path.parent / "evidence",
    )
    router = DashboardRouter(
        LedgerService(database),
        csrf_token=csrf_token,
        database=database,
        inventory=InventoryService(database),
        orders=OrderRepository(database),
        fulfillment=FulfillmentService(database),
        profit_sharing=ProfitSharingService(database),
        balances=BalanceReconciliationService(database),
        strategy_rule=StrategyRuleService(database),
        approval=ApprovalService(database),
    )
    module_pages = {
        "/optimization": (
            "广告调优",
            "查看三种 ROAS、异常证据、策略建议、审批和执行状态。",
        ),
        "/ad-funds": (
            "收入与广告资金",
            "平台收入、广告充值和广告实际消耗分账展示。",
        ),
        "/inventory": (
            "SKU与库存",
            "维护 SKU 成本版本、采购入库、库存流水和订单成本快照。",
        ),
        "/profit-sharing": (
            "合伙人分润",
            "按经营账生成利润期间并依据生效协议记录应分和实付。",
        ),
        "/strategy-rules": (
            "策略规则",
            "管理策略规则版本、参数详情和生效日期切换。",
        ),
    }

    # 模块页面渲染器映射：将模块路径指向真实数据渲染函数
    module_renderers = {
        "/optimization": render_optimization_page,
        "/ad-funds": render_ad_funds_page,
        "/inventory": render_inventory_page,
        "/profit-sharing": render_profit_sharing_page,
        "/strategy-rules": render_strategy_rules_page,
        "/approvals": render_approvals_page,
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            requested_values = query.get("date")
            requested = requested_values[0] if requested_values else None
            try:
                data_date = _resolve_dashboard_date(
                    database,
                    requested=requested,
                    fallback=default_date,
                )
            except ValueError:
                self.send_error(400, "date must use YYYY-MM-DD")
                return
            if parsed.path.startswith("/evidence/"):
                evidence_id = parsed.path.removeprefix("/evidence/")
                if not evidence_id or "/" in evidence_id:
                    self.send_error(404)
                    return
                try:
                    download = evidence.read(evidence_id)
                except EvidenceError as error:
                    status = 404 if error.code == "evidence_not_found" else 409
                    self.send_error(status, error.code)
                    return
                self.send_response(200)
                for name, value in download.headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(download.content)))
                self.end_headers()
                self.wfile.write(download.content)
                return
            if parsed.path == "/api/snapshot":
                body = snapshot_json(ReportReadModel(database).daily(data_date))
                content_type = "application/json; charset=utf-8"
            elif parsed.path == "/":
                effective_simulated = (
                    ReportReadModel(database).is_simulated(data_date)
                    if simulated is None
                    else simulated
                )
                body = render_dashboard(
                    database,
                    data_date,
                    simulated=effective_simulated,
                    platform=query.get("platform", [""])[0],
                    store=query.get("store", [""])[0],
                    campaign=query.get("campaign", [""])[0],
                    sku=query.get("sku", [""])[0],
                ).encode("utf-8")
                content_type = "text/html; charset=utf-8"
            elif parsed.path == "/operations":
                body = render_operations_page(
                    csrf_token, database, data_date
                ).encode("utf-8")
                content_type = "text/html; charset=utf-8"
            elif parsed.path == "/operating-statement":
                raw_year = query.get("year", [str(data_date.year)])[0]
                try:
                    year = int(raw_year)
                    if not 2000 <= year <= 2100:
                        raise ValueError
                except ValueError:
                    self.send_error(400, "invalid year")
                    return
                body = render_operating_statement_page(
                    database, year=year, csrf_token=csrf_token
                ).encode("utf-8")
                content_type = "text/html; charset=utf-8"
            elif parsed.path == "/knowledge":
                query = parse_qs(parsed.query).get("q", [""])[0]
                body = render_knowledge_page(
                    database, query=query
                ).encode("utf-8")
                content_type = "text/html; charset=utf-8"
            elif parsed.path.startswith("/knowledge/document"):
                doc_id = parse_qs(parsed.query).get("id", [""])[0]
                if doc_id:
                    body = render_knowledge_detail(
                        database, doc_id
                    ).encode("utf-8")
                    content_type = "text/html; charset=utf-8"
                else:
                    self.send_error(400, "document id required")
                    return
            elif parsed.path in module_renderers:
                if parsed.path == "/strategy-rules":
                    query_params = parse_qs(parsed.query)
                    shadow_results = query_params.get(
                        "shadow_results", [""]
                    )[0]
                    shadow_error = query_params.get(
                        "shadow_error", [""]
                    )[0]
                    rendered = render_strategy_rules_page(
                        database, data_date, csrf_token=csrf_token,
                        shadow_results=shadow_results,
                        shadow_error=shadow_error,
                    )
                elif parsed.path in {
                    "/ad-funds",
                    "/inventory",
                    "/profit-sharing",
                    "/approvals",
                }:
                    rendered = render_inventory_page(
                        database,
                        data_date,
                        csrf_token=csrf_token,
                        notice=parse_qs(parsed.query).get(
                            "recalc_message", [""]
                        )[0],
                        notice_level=parse_qs(parsed.query).get(
                            "recalc_status", [""]
                        )[0],
                    ) if parsed.path == "/inventory" else module_renderers[
                        parsed.path
                    ](
                        database, data_date, csrf_token=csrf_token
                    )
                else:
                    rendered = module_renderers[parsed.path](
                        database, data_date
                    )
                body = rendered.encode("utf-8")
                content_type = "text/html; charset=utf-8"
            elif parsed.path in module_pages:
                title, summary = module_pages[parsed.path]
                body = render_module_page(
                    parsed.path, title, summary
                ).encode("utf-8")
                content_type = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            path = urlparse(self.path).path
            if path == "/evidence":
                if length <= 0 or length > 5 * 1024 * 1024 + 131_072:
                    self.send_error(400, "invalid evidence request body")
                    return
                try:
                    upload = _parse_evidence_multipart(
                        self.headers.get("Content-Type", ""),
                        self.rfile.read(length),
                    )
                    if upload.fields.get("csrf_token") != csrf_token:
                        self.send_error(403, "invalid CSRF token")
                        return
                    object_type, object_id = upload.fields["target"].split(
                        ":", 1
                    )
                    evidence.attach(
                        object_type=object_type,
                        object_id=object_id,
                        display_name=upload.filename,
                        media_type=upload.media_type,
                        content=upload.content,
                        note=upload.fields.get("note", ""),
                        created_by="local-web",
                    )
                except (KeyError, ValueError, EvidenceError) as error:
                    message = (
                        error.code
                        if isinstance(error, EvidenceError)
                        else "invalid evidence upload"
                    )
                    self.send_error(400, message)
                    return
                return_to = upload.fields.get("return_to", "/ad-funds")
                if return_to not in {
                    "/ad-funds",
                    "/inventory",
                    "/profit-sharing",
                }:
                    return_to = "/ad-funds"
                self.send_response(303)
                self.send_header("Location", return_to)
                self.end_headers()
                return
            if length <= 0 or length > 1_000_000:
                self.send_error(400, "invalid request body")
                return
            raw = self.rfile.read(length).decode("utf-8")
            form = {
                key: values[-1]
                for key, values in parse_qs(raw, keep_blank_values=True).items()
            }
            response = router.post(path, form)
            if response.status == 303 and response.location:
                self.send_response(303)
                self.send_header("Location", response.location)
                self.end_headers()
                return
            self.send_error(response.status, response.message)

    ThreadingHTTPServer((host, port), Handler).serve_forever()
