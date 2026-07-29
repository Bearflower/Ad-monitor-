from __future__ import annotations

import html
import json
import secrets
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from adwatch.dashboard.routes import DashboardRouter
from adwatch.dashboard.views import (
    render_module_page,
    render_navigation,
    render_operations_page,
)
from adwatch.inventory.service import InventoryService
from adwatch.ledger.service import LedgerService
from adwatch.orders.fulfillment import FulfillmentService
from adwatch.orders.repository import OrderRepository
from adwatch.profit_sharing.service import ProfitSharingService
from adwatch.reporting.read_model import DailySnapshot, ReportReadModel
from adwatch.storage.db import Database


def _format_decimal(value: Decimal | None, places: int = 2) -> str:
    return "N/A" if value is None else f"{value:.{places}f}"


def _format_cny(value: Decimal | None) -> str:
    if value is None:
        return "待补数据"
    sign = "-" if value < 0 else ""
    return f"{sign}¥{abs(value):.2f}"


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
        <article class="card">
          <div class="eyebrow">{html.escape(item.platform.title())}</div>
          <h2>{_format_decimal(item.roas)} <span>ROAS</span></h2>
          <dl>
            <div><dt>消耗</dt><dd>{_format_decimal(item.spend)}</dd></div>
            <div><dt>GMV</dt><dd>{_format_decimal(item.gmv)}</dd></div>
            <div><dt>订单</dt><dd>{item.orders}</dd></div>
            <div><dt>净利润</dt><dd>{_format_cny(item.net_profit)}</dd></div>
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
          <td class="number">{_format_decimal(item.roas)}</td>
          <td class="number">{_format_cny(item.net_profit)}</td>
        </tr>
        """
        for item in sku_items
    )
    alerts = "".join(
        f"<li><strong>{html.escape(item['severity'])}</strong> "
        f"{html.escape(item['message'])}</li>"
        for item in snapshot.alerts
    ) or "<li>当前无异常告警</li>"
    recommendations = "".join(
        f"<li><strong>{html.escape(item['action'])}</strong> "
        f"{html.escape(item['platform'])}/{html.escape(item['campaign_id'])} — "
        f"{html.escape(item['reason'])}</li>"
        for item in snapshot.recommendations
    ) or "<li>当前无策略建议</li>"
    source_badge = "模拟数据" if simulated else "真实数据"
    scope_label = platform.title() if platform else "TikTok + Shopee"
    trends = "".join(
        "<section class=\"panel\"><h2>"
        f"{days} 天趋势</h2><div class=\"table-wrap\"><table>"
        "<thead><tr><th>日期</th><th>消耗</th><th>GMV</th><th>ROAS</th>"
        "<th>净利润</th></tr></thead><tbody>"
        + (
            "".join(
                "<tr>"
                f"<td>{point.data_date.isoformat()}</td>"
                f"<td>{_format_decimal(point.spend)}</td>"
                f"<td>{_format_decimal(point.gmv)}</td>"
                f"<td>{_format_decimal(point.roas)}</td>"
                f"<td>{_format_cny(point.net_profit)}</td>"
                "</tr>"
                for point in points
            )
            or '<tr><td colspan="5">暂无数据</td></tr>'
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
  <title>Adwatch 投放驾驶舱</title>
  <style>
    :root {{ --primary:#1e40af; --secondary:#3b82f6; --accent:#d97706;
      --background:#f8fafc; --surface:#fff; --text:#172554;
      --muted:#52647f; --border:#dbeafe; --danger:#b91c1c; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--text); background:var(--background);
      font:16px/1.55 "Fira Sans", ui-sans-serif, system-ui, sans-serif; }}
    .skip {{ position:absolute; left:-9999px; }}
    .skip:focus {{ left:16px; top:16px; z-index:10; padding:12px;
      background:var(--surface); outline:3px solid var(--accent); }}
    header,main {{ width:min(1180px, calc(100% - 32px)); margin:auto; }}
    header {{ padding:32px 0 20px; display:flex; gap:16px;
      justify-content:space-between; align-items:end; }}
    h1 {{ margin:0; font-size:clamp(1.75rem,4vw,2.6rem); letter-spacing:-.03em; }}
    .subtitle {{ color:var(--muted); margin:4px 0 0; }}
    .badge {{ background:#fff7ed; color:#9a3412; border:1px solid #fed7aa;
      padding:6px 10px; border-radius:999px; font-weight:700; white-space:nowrap; }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }}
    .card,.panel {{ background:var(--surface); border:1px solid var(--border);
      border-radius:14px; box-shadow:0 8px 24px rgba(30,64,175,.06); }}
    .card {{ padding:20px; }}
    .eyebrow {{ color:var(--primary); font-weight:700; text-transform:uppercase; }}
    h2 {{ font:700 2rem/1.1 "Fira Code",ui-monospace,monospace; margin:12px 0 20px; }}
    h2 span {{ font:500 .8rem/1 sans-serif; color:var(--muted); }}
    dl {{ display:grid; grid-template-columns:repeat(2,1fr); gap:12px; margin:0; }}
    dl div {{ border-top:1px solid var(--border); padding-top:8px; }}
    dt {{ color:var(--muted); font-size:.8rem; }}
    dd {{ margin:0; font-variant-numeric:tabular-nums; }}
    .panel {{ margin-top:16px; padding:20px; overflow:hidden; }}
    .panel h2 {{ font:700 1.15rem/1.3 sans-serif; margin:0 0 16px; }}
    .table-wrap {{ overflow:auto; }}
    table {{ width:100%; border-collapse:collapse; min-width:720px; }}
    th,td {{ padding:10px 12px; text-align:left;
      border-bottom:1px solid var(--border); }}
    th {{ color:var(--muted); font-size:.78rem; text-transform:uppercase; }}
    tbody tr:hover {{ background:#eff6ff; }}
    .number {{ font-family:"Fira Code",monospace; }}
    .lists {{ display:grid; grid-template-columns:1fr 1fr; gap:16px;
      margin-bottom:32px; }}
    ul {{ margin:0; padding-left:20px; }} li+li {{ margin-top:8px; }}
    @media (max-width:700px) {{ header {{ align-items:start; flex-direction:column; }}
      .grid,.lists {{ grid-template-columns:1fr; }} }}
    @media (prefers-reduced-motion:reduce) {{ * {{ scroll-behavior:auto!important; }} }}
  </style>
</head>
<body>
  <a class="skip" href="#main">跳到主要内容</a>
  <header><div><h1>投放驾驶舱</h1>
    <p class="subtitle">{data_date.isoformat()} · {scope_label}</p></div>
    <span class="badge">{source_badge}</span></header>
  <main id="main">
    {render_navigation("/")}
    <form method="get" class="panel" aria-label="筛选">
      <label>日期 <input name="date" type="date"
        value="{data_date.isoformat()}"></label>
      <label>平台 <select name="platform">
        <option value="">全部</option>
        <option value="tiktok">TikTok</option>
        <option value="shopee">Shopee</option>
      </select></label>
      <label>店铺 <input name="store" value="{html.escape(store)}"></label>
      <label>Campaign <input name="campaign"
        value="{html.escape(campaign)}"></label>
      <label>SKU <input name="sku" value="{html.escape(sku)}"></label>
      <button type="submit">筛选</button>
    </form>
    <section class="grid" aria-label="平台核心指标">{cards}</section>
    <section class="panel"><h2>Campaign 与 SKU 表现</h2>
      <div class="table-wrap"><table><thead><tr><th>平台</th><th>店铺</th>
      <th>Campaign</th><th>SKU</th><th>ROAS</th><th>净利润</th>
      </tr></thead><tbody>{rows}</tbody></table></div></section>
    {trends}
    <section class="panel"><h2>采集运行质量</h2>
      <div class="table-wrap"><table><thead><tr><th>平台</th><th>状态</th>
      <th>写入</th><th>隔离</th></tr></thead><tbody>{run_rows}</tbody>
      </table></div></section>
    <section class="panel"><h2>审批与执行状态</h2>
      <p>审批：{approval_summary}</p><p>执行：{execution_summary}</p></section>
    <div class="lists">
      <section class="panel"><h2>异常告警</h2><ul>{alerts}</ul></section>
      <section class="panel"><h2>策略建议（只读）</h2>
        <ul>{recommendations}</ul></section>
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
) -> None:
    csrf_token = secrets.token_urlsafe(32)
    router = DashboardRouter(
        LedgerService(database),
        csrf_token=csrf_token,
        inventory=InventoryService(database),
        orders=OrderRepository(database),
        fulfillment=FulfillmentService(database),
        profit_sharing=ProfitSharingService(database),
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
        "/approvals": (
            "审批执行",
            "查看飞书审批、Shadow、Live、回滚和熔断审计。",
        ),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            requested = parse_qs(parsed.query).get(
                "date", [default_date.isoformat()]
            )[0]
            try:
                data_date = date.fromisoformat(requested)
            except ValueError:
                self.send_error(400, "date must use YYYY-MM-DD")
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
                    platform=parse_qs(parsed.query).get("platform", [""])[0],
                    store=parse_qs(parsed.query).get("store", [""])[0],
                    campaign=parse_qs(parsed.query).get("campaign", [""])[0],
                    sku=parse_qs(parsed.query).get("sku", [""])[0],
                ).encode("utf-8")
                content_type = "text/html; charset=utf-8"
            elif parsed.path == "/operations":
                body = render_operations_page(csrf_token).encode("utf-8")
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
            if length <= 0 or length > 1_000_000:
                self.send_error(400, "invalid request body")
                return
            raw = self.rfile.read(length).decode("utf-8")
            form = {
                key: values[-1]
                for key, values in parse_qs(raw, keep_blank_values=True).items()
            }
            response = router.post(urlparse(self.path).path, form)
            if response.status == 303 and response.location:
                self.send_response(303)
                self.send_header("Location", response.location)
                self.end_headers()
                return
            self.send_error(response.status, response.message)

    ThreadingHTTPServer((host, port), Handler).serve_forever()
