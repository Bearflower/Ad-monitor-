from __future__ import annotations

import html
import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from adwatch.reporting.read_model import DailySnapshot, ReportReadModel
from adwatch.storage.db import Database


def _format_decimal(value: Decimal | None, places: int = 2) -> str:
    return "N/A" if value is None else f"{value:.{places}f}"


def render_dashboard(
    database: Database, data_date: date, *, simulated: bool
) -> str:
    snapshot = ReportReadModel(database).daily(data_date)
    cards = "".join(
        f"""
        <article class="card">
          <div class="eyebrow">{html.escape(item.platform.title())}</div>
          <h2>{_format_decimal(item.roas)} <span>ROAS</span></h2>
          <dl>
            <div><dt>消耗</dt><dd>{_format_decimal(item.spend)}</dd></div>
            <div><dt>GMV</dt><dd>{_format_decimal(item.gmv)}</dd></div>
            <div><dt>订单</dt><dd>{item.orders}</dd></div>
            <div><dt>净利润</dt><dd>{_format_decimal(item.net_profit)}</dd></div>
          </dl>
        </article>
        """
        for item in snapshot.platforms
    )
    rows = "".join(
        f"""
        <tr>
          <td>{html.escape(item.platform.title())}</td>
          <td>{html.escape(item.store)}</td>
          <td>{html.escape(item.campaign_id)}</td>
          <td>{html.escape(item.sku_id)}</td>
          <td class="number">{_format_decimal(item.roas)}</td>
          <td class="number">{_format_decimal(item.net_profit)}</td>
        </tr>
        """
        for item in snapshot.sku_performance
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
    dt {{ color:var(--muted); font-size:.8rem; }} dd {{ margin:0; font-variant-numeric:tabular-nums; }}
    .panel {{ margin-top:16px; padding:20px; overflow:hidden; }}
    .panel h2 {{ font:700 1.15rem/1.3 sans-serif; margin:0 0 16px; }}
    .table-wrap {{ overflow:auto; }} table {{ width:100%; border-collapse:collapse; min-width:720px; }}
    th,td {{ padding:10px 12px; text-align:left; border-bottom:1px solid var(--border); }}
    th {{ color:var(--muted); font-size:.78rem; text-transform:uppercase; }}
    tbody tr:hover {{ background:#eff6ff; }} .number {{ font-family:"Fira Code",monospace; }}
    .lists {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:32px; }}
    ul {{ margin:0; padding-left:20px; }} li+li {{ margin-top:8px; }}
    @media (max-width:700px) {{ header {{ align-items:start; flex-direction:column; }}
      .grid,.lists {{ grid-template-columns:1fr; }} }}
    @media (prefers-reduced-motion:reduce) {{ * {{ scroll-behavior:auto!important; }} }}
  </style>
</head>
<body>
  <a class="skip" href="#main">跳到主要内容</a>
  <header><div><h1>投放驾驶舱</h1>
    <p class="subtitle">{data_date.isoformat()} · TikTok + Shopee</p></div>
    <span class="badge">{source_badge}</span></header>
  <main id="main">
    <section class="grid" aria-label="平台核心指标">{cards}</section>
    <section class="panel"><h2>Campaign 与 SKU 表现</h2>
      <div class="table-wrap"><table><thead><tr><th>平台</th><th>店铺</th>
      <th>Campaign</th><th>SKU</th><th>ROAS</th><th>净利润</th>
      </tr></thead><tbody>{rows}</tbody></table></div></section>
    <div class="lists"><section class="panel"><h2>异常告警</h2><ul>{alerts}</ul></section>
    <section class="panel"><h2>策略建议（只读）</h2><ul>{recommendations}</ul></section></div>
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
    simulated: bool,
) -> None:
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
                body = render_dashboard(
                    database, data_date, simulated=simulated
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

    ThreadingHTTPServer((host, port), Handler).serve_forever()
