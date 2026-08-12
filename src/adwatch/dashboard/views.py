from __future__ import annotations

import html
import json
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from adwatch.accounting.operating_statement import OperatingStatementService
from adwatch.inventory.service import InventoryService
from adwatch.ledger.service import LedgerService
from adwatch.profit_sharing.service import ProfitSharingService
from adwatch.reconciliation.balances import BalanceReconciliationService
from adwatch.reporting.read_model import ReportReadModel
from adwatch.storage.db import Database
from adwatch.strategy.service import StrategyRuleService

NAVIGATION = (
    ("/", "今日经营"),
    ("/operating-statement", "月度经营账"),
    ("/optimization", "广告调优"),
    ("/ad-funds", "收入与广告资金"),
    ("/inventory", "SKU与库存"),
    ("/operations", "记账对账"),
    ("/profit-sharing", "合伙人分润"),
    ("/strategy-rules", "策略规则"),
    ("/knowledge", "知识库"),
    ("/approvals", "审批执行"),
)


def render_operating_statement_page(
    database: Database, *, year: int, csrf_token: str = ""
) -> str:
    statements = OperatingStatementService(database).yearly(year)
    rows = "".join(
        f"""
        <tr data-month="{item.starts_on:%Y-%m}">
          <td>{item.starts_on:%Y-%m}</td>
          <td>{_format_amount(item.revenue_cny)}</td>
          <td>{_format_amount(item.purchase_cash_cny)}</td>
          <td>{_format_amount(item.cogs_cny)}</td>
          <td>{_format_amount(item.platform_fee_cny)}</td>
          <td>{_format_amount(item.ad_recharge_paid_cny)}</td>
          <td>{_format_amount(item.ad_recharge_credited_cny)}</td>
          <td>{_format_amount(item.ad_spend_cny)}</td>
          <td>{_format_amount(item.review_cost_cny)}</td>
          <td>{_format_amount(item.operating_expense_cny)}</td>
          <td>{_format_amount(item.net_profit_cny)}</td>
          <td>{'完整' if item.completeness == 'complete' else '待补数据'}</td>
        </tr>
        """
        for item in statements
    )
    fields = (
        "revenue_cny", "purchase_cash_cny", "cogs_cny", "platform_fee_cny",
        "ad_recharge_paid_cny", "ad_recharge_credited_cny", "ad_spend_cny",
        "review_cost_cny", "operating_expense_cny",
    )
    totals = [sum((getattr(item, field) or Decimal(0) for item in statements), Decimal(0)) for field in fields]
    annual_profit = (
        None if any(item.net_profit_cny is None for item in statements)
        else sum((item.net_profit_cny for item in statements if item.net_profit_cny is not None), Decimal(0))
    )
    total_cells = "".join(f"<td>{_format_amount(value)}</td>" for value in totals)
    body = f"""
    <div class="module-hero"><h1>月度经营账</h1>
      <p>利润与现金分开核算；广告充值不计入利润，只计入现金变化。</p></div>
    <form method="get"><label>年份 <input name="year" value="{year}"></label><button>查看</button></form>
    <section class="panel"><div class="table-wrap"><table>
      <thead><tr><th>月份</th><th>收入</th><th>采购现金支出</th><th>销售成本</th>
      <th>平台费</th><th>广告充值支付</th><th>广告充值到账</th><th>广告实际消耗</th>
      <th>刷单</th><th>经营费用</th><th>净利润</th><th>完整性</th></tr></thead>
      <tbody>{rows}<tr><th>年度合计</th>{total_cells}<td>{_format_amount(annual_profit)}</td><td>—</td></tr></tbody>
    </table></div></section>
    """
    return _page_shell(
        title="月度经营账", active_path="/operating-statement",
        header_meta=f"{year} 年 · Excel 公式等价经营口径", body_content=body,
    )

PROFIT_INPUT_LABELS = {
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


def _page_styles() -> str:
    """返回所有页面共享的 CSS 样式。"""
    return """
    /* ===== Design Tokens ===== */
    :root {
      --color-primary: #4F46E5;
      --color-primary-light: #6366F1;
      --color-primary-dark: #3730A3;
      --color-primary-bg: #EEF2FF;
      --color-accent: #F59E0B;
      --color-accent-bg: #FFFBEB;
      --color-success: #10B981;
      --color-success-bg: #ECFDF5;
      --color-danger: #EF4444;
      --color-danger-bg: #FEF2F2;
      --color-text: #0F172A;
      --color-text-secondary: #475569;
      --color-text-muted: #94A3B8;
      --color-text-inverse: #FFFFFF;
      --color-bg: #F8FAFC;
      --color-bg-gradient: linear-gradient(135deg, #F8FAFC 0%, #EEF2FF 100%);
      --color-surface: #FFFFFF;
      --color-border: #E2E8F0;
      --color-border-light: #F1F5F9;
      --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.05);
      --shadow-md: 0 4px 12px rgba(15, 23, 42, 0.06);
      --shadow-lg: 0 8px 24px rgba(15, 23, 42, 0.08);
      --shadow-card-hover: 0 12px 32px rgba(79, 70, 229, 0.12);
      --radius-sm: 6px;
      --radius-md: 10px;
      --radius-lg: 14px;
      --radius-xl: 20px;
      --space-xs: 4px;
      --space-sm: 8px;
      --space-md: 16px;
      --space-lg: 24px;
      --space-xl: 32px;
      --transition-fast: 150ms ease;
      --transition-normal: 250ms ease;
      --font-sans: "Inter", "Noto Sans SC", -apple-system, BlinkMacSystemFont,
        "PingFang SC", "Microsoft YaHei", "Segoe UI", Roboto, sans-serif;
      --font-mono: "JetBrains Mono", "SF Mono", ui-monospace, monospace;
      --container-max: 1200px;
    }

    /* ===== Reset ===== */
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    /* ===== Base ===== */
    html { scroll-behavior: smooth; -webkit-font-smoothing: antialiased; }
    body {
      color: var(--color-text);
      background: var(--color-bg);
      background-image: var(--color-bg-gradient);
      background-attachment: fixed;
      font-family: var(--font-sans);
      font-size: 15px;
      line-height: 1.6;
      min-height: 100vh;
      overflow-x: hidden;
    }

    /* ===== Skip Link ===== */
    .skip { position: absolute; left: -9999px; top: auto; }
    .skip:focus {
      left: 16px; top: 16px; z-index: 100;
      padding: 12px 20px;
      background: var(--color-primary);
      color: var(--color-text-inverse);
      border-radius: var(--radius-md);
      outline: 3px solid var(--color-accent);
      font-weight: 600;
      text-decoration: none;
    }

    /* ===== Container ===== */
    header, main {
      width: min(var(--container-max), calc(100% - 32px));
      margin: 0 auto;
      min-width: 0;
    }

    /* ===== Header ===== */
    .page-header {
      padding: 32px 0 24px;
      display: flex;
      gap: var(--space-md);
      justify-content: space-between;
      align-items: flex-end;
      flex-wrap: wrap;
    }
    .page-header h1 {
      margin: 0;
      font-size: clamp(1.75rem, 4vw, 2.5rem);
      font-weight: 800;
      letter-spacing: -0.03em;
      background: linear-gradient(135deg, var(--color-primary) 0%, var(--color-primary-light) 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }
    .header-meta { margin: 6px 0 0; color: var(--color-text-muted); font-size: 0.9rem; }
    .header-actions { display: flex; align-items: center; gap: var(--space-sm); }
    .badge {
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
    }
    .badge::before {
      content: "";
      width: 6px; height: 6px;
      border-radius: 50%;
      background: var(--color-primary);
    }

    /* ===== Navigation ===== */
    .nav-bar {
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
    }
    .nav-bar a {
      position: relative;
      padding: 10px 18px;
      border-radius: var(--radius-md);
      color: var(--color-text-secondary);
      font-weight: 500;
      font-size: 0.88rem;
      text-decoration: none;
      white-space: nowrap;
      transition: color var(--transition-fast), background-color var(--transition-fast);
    }
    .nav-bar a:hover {
      color: var(--color-primary);
      background: var(--color-primary-bg);
    }
    .nav-bar a[aria-current="page"] {
      color: var(--color-text-inverse);
      background: var(--color-primary);
      box-shadow: 0 2px 8px rgba(79, 70, 229, 0.3);
    }
    .nav-bar a[aria-current="page"]::after {
      content: "";
      position: absolute;
      bottom: -6px;
      left: 50%;
      transform: translateX(-50%);
      width: 20px;
      height: 3px;
      background: var(--color-accent);
      border-radius: 2px;
    }

    /* ===== Panels ===== */
    .panel {
      background: var(--color-surface);
      border: 1px solid var(--color-border);
      border-radius: var(--radius-xl);
      padding: var(--space-lg);
      margin-top: var(--space-lg);
      box-shadow: var(--shadow-sm);
      min-width: 0;
    }
    .panel-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: var(--space-lg);
    }
    .panel-header h2 {
      font-size: 1.1rem;
      font-weight: 700;
      color: var(--color-text);
      margin: 0;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .panel-header h2::before {
      content: "";
      width: 4px;
      height: 20px;
      background: linear-gradient(180deg, var(--color-primary), var(--color-primary-light));
      border-radius: 2px;
    }
    .panel-header .panel-desc {
      color: var(--color-text-muted);
      font-size: 0.85rem;
      font-weight: normal;
    }

    /* ===== Form Layout ===== */
    .form-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: var(--space-md);
    }
    .form-field {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .form-field label {
      font-size: 0.82rem;
      font-weight: 600;
      color: var(--color-text-secondary);
    }
    .form-field input,
    .form-field select,
    .form-field textarea {
      width: 100%;
      padding: 10px 14px;
      border: 1px solid var(--color-border);
      border-radius: var(--radius-md);
      background: var(--color-surface);
      color: var(--color-text);
      font-family: var(--font-sans);
      font-size: 0.9rem;
      transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
    }
    .form-field input:focus,
    .form-field select:focus,
    .form-field textarea:focus {
      outline: none;
      border-color: var(--color-primary);
      box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.15);
    }
    .form-field textarea {
      resize: vertical;
      min-height: 80px;
    }
    .form-field.checkbox-field {
      flex-direction: row;
      align-items: center;
      gap: 10px;
      padding: 10px 0;
    }
    .form-field.checkbox-field input[type="checkbox"] {
      width: 18px;
      height: 18px;
      accent-color: var(--color-primary);
    }
    .form-field.checkbox-field label {
      font-weight: 500;
      cursor: pointer;
    }

    /* ===== Form Section (Grouped Forms) ===== */
    .form-section {
      background: var(--color-bg);
      border: 1px solid var(--color-border-light);
      border-radius: var(--radius-lg);
      padding: var(--space-lg);
      margin-bottom: var(--space-lg);
    }
    .form-section-title {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 1rem;
      font-weight: 700;
      color: var(--color-text);
      margin-bottom: var(--space-md);
      padding-bottom: var(--space-sm);
      border-bottom: 1px solid var(--color-border);
    }
    .form-section-title::before {
      content: "";
      width: 4px;
      height: 18px;
      background: var(--color-primary);
      border-radius: 2px;
    }
    .form-section-desc {
      color: var(--color-text-muted);
      font-size: 0.85rem;
      margin-bottom: var(--space-md);
    }

    /* ===== Embedded Forms (within sections) ===== */
    .embedded-form {
      background: var(--color-surface);
      border: 1px solid var(--color-border);
      border-radius: var(--radius-md);
      padding: var(--space-md);
      margin-bottom: var(--space-md);
    }
    .embedded-form:last-child {
      margin-bottom: 0;
    }
    .embedded-form-title {
      font-size: 0.9rem;
      font-weight: 600;
      color: var(--color-text-secondary);
      margin-bottom: var(--space-sm);
    }

    /* ===== Buttons ===== */
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 10px 24px;
      background: var(--color-primary);
      color: var(--color-text-inverse);
      border: none;
      border-radius: var(--radius-md);
      font-weight: 600;
      font-size: 0.9rem;
      font-family: var(--font-sans);
      cursor: pointer;
      transition: background-color var(--transition-fast), transform var(--transition-fast), box-shadow var(--transition-fast);
      text-decoration: none;
    }
    .btn:hover {
      background: var(--color-primary-dark);
      transform: translateY(-1px);
      box-shadow: 0 4px 12px rgba(79, 70, 229, 0.3);
    }
    .btn-secondary {
      background: var(--color-surface);
      color: var(--color-primary);
      border: 1px solid var(--color-primary);
    }
    .btn-secondary:hover {
      background: var(--color-primary-bg);
      box-shadow: 0 4px 12px rgba(79, 70, 229, 0.15);
    }
    .btn-success {
      background: var(--color-success);
    }
    .btn-success:hover {
      background: #059669;
    }
    .btn-sm {
      padding: 6px 14px;
      font-size: 0.82rem;
    }

    /* Form action bar */
    .form-actions {
      display: flex;
      gap: var(--space-sm);
      justify-content: flex-end;
      margin-top: var(--space-md);
      padding-top: var(--space-md);
      border-top: 1px solid var(--color-border-light);
    }

    /* ===== KPI Detail Grid (for optimization center) ===== */
    .kpi-details {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 12px;
      margin: 0;
    }
    .kpi-detail {
      padding: 12px;
      background: var(--color-bg);
      border-radius: var(--radius-md);
      border: 1px solid var(--color-border-light);
      transition: background-color var(--transition-fast);
    }
    .kpi-detail:hover {
      background: var(--color-primary-bg);
    }
    .kpi-detail dt {
      color: var(--color-text-muted);
      font-size: 0.78rem;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin-bottom: 4px;
    }
    .kpi-detail dd {
      margin: 0;
      font-size: 1.05rem;
      font-weight: 700;
      color: var(--color-text);
      font-variant-numeric: tabular-nums;
    }

    /* ===== Tables ===== */
    .table-wrap {
      overflow-x: auto;
      border-radius: var(--radius-lg);
      border: 1px solid var(--color-border-light);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 600px;
      font-size: 0.88rem;
    }
    thead { background: var(--color-bg); }
    th {
      padding: 14px 16px;
      text-align: left;
      color: var(--color-text-muted);
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      white-space: nowrap;
      border-bottom: 2px solid var(--color-border);
    }
    td {
      padding: 12px 16px;
      border-bottom: 1px solid var(--color-border-light);
      color: var(--color-text);
    }
    tbody tr { transition: background-color var(--transition-fast); }
    tbody tr:hover { background: var(--color-primary-bg); }
    tbody tr:last-child td { border-bottom: none; }
    .number {
      font-family: var(--font-mono);
      font-variant-numeric: tabular-nums;
      font-weight: 600;
    }
    .text-profit-positive { color: var(--color-success); }
    .text-profit-negative { color: var(--color-danger); }
    .text-roas { color: var(--color-accent); font-weight: 700; }

    /* ===== Module Hero ===== */
    .module-hero {
      background: linear-gradient(135deg, var(--color-primary) 0%, var(--color-primary-light) 100%);
      border-radius: var(--radius-xl);
      padding: var(--space-xl);
      margin-top: var(--space-lg);
      color: var(--color-text-inverse);
      position: relative;
      overflow: hidden;
    }
    .module-hero::before {
      content: "";
      position: absolute;
      top: -50%; right: -30%;
      width: 400px; height: 400px;
      background: radial-gradient(circle, rgba(255,255,255,0.15) 0%, transparent 70%);
      pointer-events: none;
    }
    .module-hero h1 {
      font-size: clamp(1.5rem, 3vw, 2rem);
      font-weight: 800;
      margin: 0 0 8px;
      position: relative;
      z-index: 1;
    }
    .module-hero p {
      font-size: 0.95rem;
      opacity: 0.9;
      margin: 0;
      position: relative;
      z-index: 1;
      max-width: 600px;
    }
    .module-hero .hero-stats {
      display: flex;
      gap: var(--space-xl);
      margin-top: var(--space-lg);
      position: relative;
      z-index: 1;
    }
    .module-hero .hero-stat .stat-value {
      font-size: 2rem;
      font-weight: 800;
      font-variant-numeric: tabular-nums;
    }
    .module-hero .hero-stat .stat-label {
      font-size: 0.8rem;
      opacity: 0.8;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }

    /* ===== Module Feature Cards ===== */
    .feature-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: var(--space-lg);
      margin-top: var(--space-lg);
    }
    .feature-card {
      background: var(--color-surface);
      border: 1px solid var(--color-border);
      border-radius: var(--radius-xl);
      padding: var(--space-lg);
      box-shadow: var(--shadow-md);
      transition: transform var(--transition-normal), box-shadow var(--transition-normal);
    }
    .feature-card:hover {
      transform: translateY(-4px);
      box-shadow: var(--shadow-card-hover);
    }
    .feature-icon {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 44px; height: 44px;
      background: var(--color-primary-bg);
      color: var(--color-primary);
      border-radius: var(--radius-md);
      font-size: 1.3rem;
      margin-bottom: var(--space-md);
    }
    .feature-card h3 {
      font-size: 1.05rem;
      font-weight: 700;
      color: var(--color-text);
      margin: 0 0 6px;
    }
    .feature-card p {
      color: var(--color-text-muted);
      font-size: 0.88rem;
      margin: 0 0 var(--space-md);
    }
    .feature-card .feature-link {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      color: var(--color-primary);
      font-weight: 600;
      font-size: 0.85rem;
      text-decoration: none;
      transition: gap var(--transition-fast);
    }
    .feature-card .feature-link:hover {
      gap: 8px;
    }

    /* ===== Responsive ===== */
    @media (max-width: 900px) {
      .module-hero .hero-stats { flex-wrap: wrap; gap: var(--space-md); }
    }
    @media (max-width: 700px) {
      .page-header {
        flex-direction: column;
        align-items: flex-start;
        gap: var(--space-md);
      }
      .nav-bar { padding: 4px; gap: 2px; }
      .nav-bar a { padding: 8px 14px; font-size: 0.82rem; }
      .form-grid { grid-template-columns: 1fr; }
      .panel { padding: var(--space-md); }
      .form-section { padding: var(--space-md); }
      .module-hero { padding: var(--space-lg); }
      th, td { padding: 10px 12px; font-size: 0.82rem; }
    }
    @media (max-width: 480px) {
      .form-actions { flex-direction: column-reverse; }
      .form-actions .btn { width: 100%; }
    }

    /* ===== Empty State ===== */
    .empty-state {
      text-align: center;
      padding: 32px 20px;
      color: var(--color-text-muted);
      font-size: 0.9rem;
    }

    /* ===== List Panel (Alerts & Recommendations) ===== */
    .list-panel ul {
      margin: 0;
      padding: 0;
      list-style: none;
    }
    .list-panel li {
      padding: 12px 16px;
      border-radius: var(--radius-md);
      background: var(--color-bg);
      margin-bottom: 8px;
      display: flex;
      gap: 10px;
      align-items: flex-start;
      transition: background-color var(--transition-fast);
    }
    .list-panel li:hover {
      background: var(--color-primary-bg);
    }
    .list-panel li.empty-row {
      justify-content: center;
      color: var(--color-text-muted);
      background: none;
    }
    .list-panel li strong {
      flex-shrink: 0;
      padding: 2px 8px;
      border-radius: 6px;
      font-size: 0.72rem;
      font-weight: 700;
      text-transform: uppercase;
    }
    .list-panel li .severity-high {
      background: var(--color-danger-bg);
      color: var(--color-danger);
    }
    .list-panel li .severity-medium {
      background: var(--color-accent-bg);
      color: var(--color-accent);
    }
    .list-panel li .severity-low {
      background: var(--color-success-bg);
      color: var(--color-success);
    }
    .list-panel li .severity-info {
      background: var(--color-primary-bg);
      color: var(--color-primary);
    }
    .list-panel li span {
      color: var(--color-text-secondary);
      font-size: 0.88rem;
      line-height: 1.5;
    }

    /* ===== Reduced Motion ===== */
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after {
        animation-duration: 0.01ms !important;
        transition-duration: 0.01ms !important;
      }
    }
    """


def render_navigation(active_path: str) -> str:
    """渲染导航栏。"""
    links = []
    for path, label in NAVIGATION:
        current = ' aria-current="page"' if path == active_path else ""
        links.append(
            f'<a href="{path}"{current}>{html.escape(label)}</a>'
        )
    return '<nav class="nav-bar" aria-label="业务导航">' + "".join(links) + "</nav>"


def _page_shell(
    *,
    title: str,
    active_path: str,
    header_title: str | None = None,
    header_meta: str = "",
    badge_text: str | None = None,
    body_content: str = "",
) -> str:
    """生成统一的页面 HTML 骨架。"""
    styles = _page_styles()
    nav = render_navigation(active_path)
    actual_title = header_title or title
    badge_html = f'<span class="badge">{badge_text}</span>' if badge_text else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} - AdWatch</title>
  <style>{styles}</style>
</head>
<body>
  <a class="skip" href="#main">跳到主要内容</a>
  <header class="page-header">
    <div>
      <h1>{html.escape(actual_title)}</h1>
      {f'<p class="header-meta">{header_meta}</p>' if header_meta else ''}
    </div>
    <div class="header-actions">{badge_html}</div>
  </header>
  <main id="main">
    {nav}
    {body_content}
  </main>
</body>
</html>"""


def render_operations_forms(csrf_token: str) -> str:
    """渲染记账对账页面的所有表单。"""
    token = html.escape(csrf_token, quote=True)
    return f"""
    <!-- 费用录入 -->
    <section class="form-section">
      <div class="form-section-title">费用／前期投入</div>
      <div class="form-section-desc">记录日常经营费用、前期投入和资金调整</div>
      <form method="post" action="/expenses" class="embedded-form">
        <input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid">
          <div class="form-field">
            <label>日期</label>
            <input type="date" name="occurred_on" required>
          </div>
          <div class="form-field">
            <label>费用类别</label>
            <input name="category" placeholder="如：办公费、广告费" required>
          </div>
          <div class="form-field">
            <label>金额</label>
            <input name="amount" inputmode="decimal" placeholder="0.00" required>
          </div>
          <div class="form-field">
            <label>币种</label>
            <input name="currency" value="CNY" required>
          </div>
          <div class="form-field">
            <label>汇率</label>
            <input name="rate_to_cny" value="1" required>
          </div>
          <div class="form-field">
            <label>付款人</label>
            <input name="payer" placeholder="付款人姓名" required>
          </div>
          <div class="form-field">
            <label>资金性质</label>
            <select name="fund_nature">
              <option value="operating_expense">经营费用</option>
              <option value="capital_contribution">合伙人实缴</option>
              <option value="partner_advance">合伙人代垫</option>
              <option value="adjustment">资金调整</option>
            </select>
          </div>
          <div class="form-field checkbox-field">
            <input type="checkbox" name="affects_profit" value="1" id="affects_profit">
            <label for="affects_profit">计入利润</label>
          </div>
          <div class="form-field checkbox-field">
            <input type="checkbox" name="affects_capital" value="1" id="affects_capital">
            <label for="affects_capital">计入出资对账</label>
          </div>
          <div class="form-field" style="grid-column: 1 / -1;">
            <label>备注</label>
            <textarea name="note" placeholder="详细说明..."></textarea>
          </div>
        </div>
        <div class="form-actions">
          <button type="submit" class="btn">保存草稿</button>
        </div>
      </form>
    </section>

    <!-- 合伙人出资与提款 -->
    <section class="form-section">
      <div class="form-section-title">人工收入</div>
      <form method="post" action="/manual-income" class="embedded-form">
        <input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid">
          <div class="form-field"><label>日期</label><input type="date" name="occurred_on" required></div>
          <div class="form-field"><label>平台</label><input name="platform"></div>
          <div class="form-field"><label>店铺</label><input name="store"></div>
          <div class="form-field"><label>订单号</label><input name="order_id"></div>
          <div class="form-field"><label>原币金额</label><input name="amount_original" required></div>
          <div class="form-field"><label>币种</label><select name="currency"><option>THB</option><option>SGD</option><option>CNY</option></select></div>
          <div class="form-field"><label>备注</label><input name="note"></div>
        </div><button class="btn">登记收入</button>
      </form>
    </section>

    <section class="form-section">
      <div class="form-section-title">合伙人出资与提款</div>
      <div class="form-section-desc">登记合伙人的资金投入和提取记录</div>
      <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:var(--space-md);">
        <form method="post" action="/capital" class="embedded-form">
          <div class="embedded-form-title">登记出资</div>
          <input type="hidden" name="csrf_token" value="{token}">
          <div class="form-grid">
            <div class="form-field">
              <label>日期</label>
              <input type="date" name="occurred_on" required>
            </div>
            <div class="form-field">
              <label>合伙人</label>
              <input name="partner" placeholder="合伙人姓名" required>
            </div>
            <div class="form-field">
              <label>出资类型</label>
              <select name="entry_type">
                <option value="paid_in">实缴</option>
                <option value="advance">代垫</option>
              </select>
            </div>
            <div class="form-field">
              <label>投入名称</label><input name="entry_name">
            </div>
            <div class="form-field">
              <label>原币金额</label><input name="amount_original" required>
            </div>
            <div class="form-field">
              <label>币种</label><select name="currency"><option>CNY</option><option>THB</option><option>SGD</option></select>
            </div>
          </div>
          <div class="form-actions">
            <button type="submit" class="btn btn-success">登记出资</button>
          </div>
        </form>
        <form method="post" action="/withdrawals" class="embedded-form">
          <div class="embedded-form-title">登记提款</div>
          <input type="hidden" name="csrf_token" value="{token}">
          <div class="form-grid">
            <div class="form-field">
              <label>日期</label>
              <input type="date" name="occurred_on" required>
            </div>
            <div class="form-field">
              <label>提款人</label>
              <input name="partner" placeholder="提款人姓名" required>
            </div>
            <div class="form-field">
              <label>支付原币金额</label>
              <input name="paid_amount_original" required>
            </div>
            <div class="form-field">
              <label>实际到账原币金额</label>
              <input name="credited_amount_original" required>
            </div>
            <div class="form-field">
              <label>币种</label><select name="currency"><option>THB</option><option>SGD</option><option>CNY</option></select>
            </div>
            <div class="form-field">
              <label>用途</label>
              <input name="purpose" placeholder="提款用途" required>
            </div>
          </div>
          <div class="form-actions">
            <button type="submit" class="btn">登记提款</button>
          </div>
        </form>
      </div>
    </section>

    <!-- 广告充值与刷单成本 -->
    <section class="form-section">
      <div class="form-section-title">广告充值与刷单成本</div>
      <div class="form-section-desc">管理广告账户充值和刷单产生的额外成本</div>
      <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:var(--space-md);">
        <form method="post" action="/ad-funding" class="embedded-form">
          <div class="embedded-form-title">广告资金登记</div>
          <input type="hidden" name="csrf_token" value="{token}">
          <div class="form-grid">
            <div class="form-field">
              <label>日期</label>
              <input type="date" name="occurred_on" required>
            </div>
            <div class="form-field">
              <label>平台</label>
              <input name="platform" placeholder="TikTok / Shopee" required>
            </div>
            <div class="form-field">
              <label>店铺</label>
              <input name="store" placeholder="店铺名称" required>
            </div>
            <div class="form-field">
              <label>类型</label>
              <select name="entry_type">
                <option value="recharge">充值</option>
                <option value="gift">赠送</option>
                <option value="refund">退款</option>
              </select>
            </div>
            <div class="form-field">
              <label>金额 (CNY)</label>
              <input name="amount" placeholder="人民币金额" required>
            </div>
            <div class="form-field">
              <label>来源</label>
              <input name="source" value="manual" required>
            </div>
          </div>
          <div class="form-actions">
            <button type="submit" class="btn">登记广告资金</button>
          </div>
        </form>
        <form method="post" action="/review-costs" class="embedded-form">
          <div class="embedded-form-title">刷单成本登记</div>
          <input type="hidden" name="csrf_token" value="{token}">
          <div class="form-grid">
            <div class="form-field">
              <label>日期</label>
              <input type="date" name="occurred_on" required>
            </div>
            <div class="form-field">
              <label>平台</label>
              <input name="platform" placeholder="TikTok / Shopee" required>
            </div>
            <div class="form-field">
              <label>店铺</label>
              <input name="store" placeholder="店铺名称" required>
            </div>
            <div class="form-field">
              <label>订单号</label>
              <input name="order_id" placeholder="刷单订单号" required>
            </div>
            <div class="form-field">
              <label>Seller SKU</label>
              <input name="seller_sku" placeholder="选填">
            </div>
            <div class="form-field">
              <label>货款</label>
              <input name="goods_cost_original" placeholder="原币货款" required>
            </div>
            <div class="form-field">
              <label>服务费</label>
              <input name="service_fee_original" placeholder="原币服务费" required>
            </div>
            <div class="form-field">
              <label>币种</label><select name="currency"><option>CNY</option><option>THB</option><option>SGD</option></select>
            </div>
          </div>
          <div class="form-actions">
            <button type="submit" class="btn">登记并排除真实指标</button>
          </div>
        </form>
      </div>
    </section>

    <!-- SKU 成本与采购入库 -->
    <section class="form-section">
      <div class="form-section-title">SKU 成本与采购入库</div>
      <div class="form-section-desc">维护 SKU 成本版本、履约方式和采购入库记录</div>
      <form method="post" action="/fulfillment" class="embedded-form">
        <div class="embedded-form-title">履约方式版本</div>
        <input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid">
          <div class="form-field">
            <label>平台</label>
            <input name="platform" placeholder="平台" required>
          </div>
          <div class="form-field">
            <label>店铺</label>
            <input name="store" placeholder="店铺" required>
          </div>
          <div class="form-field">
            <label>Seller SKU</label>
            <input name="seller_sku" placeholder="Seller SKU" required>
          </div>
          <div class="form-field">
            <label>生效日期</label>
            <input type="date" name="effective_date" required>
          </div>
          <div class="form-field">
            <label>履约模式</label>
            <select name="mode">
              <option value="supplier_fulfilled">货盘代发</option>
              <option value="stocked">自有备货</option>
            </select>
          </div>
          <div class="form-field">
            <label>供货状态</label>
            <select name="supply_status">
              <option value="available">可供货</option>
              <option value="paused">暂停供货</option>
            </select>
          </div>
          <div class="form-field" style="grid-column: 1 / -1;">
            <label>履约备注</label>
            <input name="note" placeholder="选填">
          </div>
        </div>
        <div class="form-actions">
          <button type="submit" class="btn">新增履约方式版本</button>
        </div>
      </form>
      <form method="post" action="/sku-costs" class="embedded-form">
        <div class="embedded-form-title">SKU 成本版本</div>
        <input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid">
          <div class="form-field">
            <label>平台</label>
            <input name="platform" placeholder="平台" required>
          </div>
          <div class="form-field">
            <label>店铺</label>
            <input name="store" placeholder="店铺" required>
          </div>
          <div class="form-field">
            <label>Seller SKU</label>
            <input name="seller_sku" placeholder="Seller SKU" required>
          </div>
          <div class="form-field">
            <label>生效日期</label>
            <input type="date" name="effective_date" required>
          </div>
          <div class="form-field">
            <label>单位成本 (CNY)</label>
            <input name="unit_cost_cny" placeholder="人民币单位成本" required>
          </div>
          <div class="form-field">
            <label>备注</label>
            <input name="note" placeholder="选填">
          </div>
        </div>
        <div class="form-actions">
          <button type="submit" class="btn btn-success">新增成本版本</button>
        </div>
      </form>
      <form method="post" action="/purchases" class="embedded-form">
        <div class="embedded-form-title">采购入库</div>
        <input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid">
          <div class="form-field">
            <label>采购单号</label>
            <input name="receipt_id" placeholder="采购单号" required>
          </div>
          <div class="form-field">
            <label>供应商</label>
            <input name="supplier" placeholder="供应商名称" required>
          </div>
          <div class="form-field">
            <label>入库日期</label>
            <input type="date" name="received_on" required>
          </div>
          <div class="form-field">
            <label>Seller SKU</label>
            <input name="seller_sku" placeholder="Seller SKU" required>
          </div>
          <div class="form-field">
            <label>入库数量</label>
            <input name="quantity" placeholder="数量" required>
          </div>
          <div class="form-field">
            <label>单位成本 (CNY)</label>
            <input name="unit_cost_cny" placeholder="人民币单位成本" required>
          </div>
        </div>
        <div class="form-actions">
          <button type="submit" class="btn">采购入库</button>
        </div>
      </form>
    </section>

    <!-- 分润协议 -->
    <section class="form-section">
      <div class="form-section-title">分润协议</div>
      <div class="form-section-desc">管理合伙人分润协议版本、结算期间和支付记录</div>
      <form method="post" action="/profit-agreements" class="embedded-form">
        <div class="embedded-form-title">新增协议版本</div>
        <input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid">
          <div class="form-field">
            <label>生效日期</label>
            <input type="date" name="effective_from" required>
          </div>
          <div class="form-field">
            <label>洁云比例</label>
            <input name="jieyun_share" value="0.60" step="0.01" required>
          </div>
          <div class="form-field">
            <label>苏姐比例</label>
            <input name="sujie_share" value="0.40" step="0.01" required>
          </div>
        </div>
        <div class="form-actions">
          <button type="submit" class="btn btn-success">新增协议版本</button>
        </div>
      </form>
      <form method="post" action="/profit-periods" class="embedded-form">
        <div class="embedded-form-title">生成分润草稿</div>
        <input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid">
          <div class="form-field">
            <label>结算开始</label>
            <input type="date" name="starts_on" required>
          </div>
          <div class="form-field">
            <label>结算结束</label>
            <input type="date" name="ends_on" required>
          </div>
        </div>
        <div class="form-actions">
          <button type="submit" class="btn">按经营账生成分润草稿</button>
        </div>
      </form>
      <form method="post" action="/profit-payments" class="embedded-form">
        <div class="embedded-form-title">记录分润支付</div>
        <input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid">
          <div class="form-field">
            <label>分润期间 ID</label>
            <input name="period_id" placeholder="期间 ID" required>
          </div>
          <div class="form-field">
            <label>收款人</label>
            <input name="partner" placeholder="收款人姓名" required>
          </div>
          <div class="form-field">
            <label>实付金额 (CNY)</label>
            <input name="amount_cny" placeholder="实付人民币" required>
          </div>
          <div class="form-field">
            <label>支付日期</label>
            <input type="date" name="paid_on" required>
          </div>
          <div class="form-field">
            <label>状态</label>
            <select name="status">
              <option value="planned">计划</option>
              <option value="paid">已支付</option>
            </select>
          </div>
          <div class="form-field">
            <label>备注</label>
            <input name="note" placeholder="选填">
          </div>
        </div>
        <div class="form-actions">
          <button type="submit" class="btn">记录分润支付</button>
        </div>
      </form>
      <form method="post" action="/profit-periods/confirm" class="embedded-form">
        <div class="embedded-form-title">确认分润期间</div>
        <input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid">
          <div class="form-field">
            <label>分润期间 ID</label>
            <input name="period_id" placeholder="期间 ID" required>
          </div>
        </div>
        <div class="form-actions">
          <button type="submit" class="btn btn-success">确认分润期间</button>
        </div>
      </form>
    </section>
    """


def render_operations_page(
    csrf_token: str,
    database: Database | None = None,
    data_date: date | None = None,
) -> str:
    """渲染记账对账页面。"""
    selected_date = data_date or datetime.now(UTC).date()
    rate_rows = ()
    if database is not None:
        from adwatch.accounting.exchange_rates import (
            AccountingExchangeRateService,
        )

        rate_rows = AccountingExchangeRateService(database).list_monthly_rates(
            selected_date.year
        )
    rate_table = "".join(
        f"<tr><td>{html.escape(item['year_month'])}</td>"
        f"<td>{html.escape(item['currency'])} → CNY</td>"
        f"<td>{html.escape(item['rate_to_cny'])}</td>"
        f"<td>{html.escape(item['source'])}</td></tr>"
        for item in rate_rows
    ) or '<tr><td colspan="4">暂无月度汇率覆盖</td></tr>'
    body = f"""
    <div class="module-hero">
      <h1>记账对账</h1>
      <p>一站式管理费用录入、合伙人资金、广告成本、SKU成本和分润协议</p>
    </div>
    <section class="panel"><div class="panel-header"><h2>月度汇率</h2></div>
      <form method="post" action="/monthly-exchange-rates" class="embedded-form">
        <input type="hidden" name="csrf_token" value="{html.escape(csrf_token, quote=True)}">
        <div class="form-grid">
          <div class="form-field"><label>月份</label><input name="year_month" value="{selected_date:%Y-%m}" required></div>
          <div class="form-field"><label>币种</label><select name="currency"><option>THB</option><option>SGD</option><option>CNY</option></select></div>
          <div class="form-field"><label>兑人民币汇率</label><input name="rate_to_cny" required></div>
          <div class="form-field"><label>来源</label><input name="source" value="manual" required></div>
          <div class="form-field"><label>备注</label><input name="note"></div>
        </div><button class="btn">保存月度汇率</button>
      </form>
      <div class="table-wrap"><table><thead><tr><th>月份</th><th>币种</th><th>汇率</th><th>来源</th></tr></thead><tbody>{rate_table}</tbody></table></div>
    </section>
    {render_operations_forms(csrf_token)}
    """
    return _page_shell(
        title="记账对账",
        active_path="/operations",
        header_title="记账对账",
        header_meta="财务数据录入与管理中心",
        badge_text="Operations",
        body_content=body,
    )


def render_module_page(path: str, title: str, summary: str) -> str:
    """渲染模块功能页面。"""
    # 根据路径生成不同的功能卡片内容
    module_features = {
        "/optimization": (
            [
                ("🎯", "三种 ROAS 视图", "平台 ROAS、净销售 ROAS 和利润 ROAS 全方位对比"),
                ("🔍", "异常证据分析", "自动收集异常数据证据，辅助决策"),
                ("⚡", "策略建议引擎", "基于数据的智能优化建议，支持一键审批执行"),
                ("🛡️", "审批与执行审计", "完整的操作审计追踪，支持回滚和熔断"),
            ]
        ),
        "/ad-funds": (
            [
                ("💰", "平台收入总览", "TikTok 和 Shopee 平台收入实时汇总"),
                ("📊", "广告充值管理", "分账户追踪充值、赠送和退款记录"),
                ("📈", "消耗分账展示", "实际广告消耗按账户和 Campaign 拆分"),
                ("💵", "资金流水对账", "收入与消耗自动对账，标记异常"),
            ]
        ),
        "/inventory": (
            [
                ("📦", "SKU 成本版本", "按日期维护 SKU 单位成本的历史版本"),
                ("🏷️", "采购入库管理", "登记采购入库单，自动更新库存流水"),
                ("🔄", "库存流水追踪", "实时追踪 SKU 出入库和当前库存状态"),
                ("📋", "订单成本快照", "订单级别的成本快照，支持利润精确计算"),
            ]
        ),
        "/profit-sharing": (
            [
                ("📐", "分润协议管理", "支持多版本协议，按生效日期自动切换"),
                ("📅", "利润期间生成", "按经营账自动生成分润结算期间"),
                ("💸", "支付记录追踪", "记录和追踪分润支付状态（计划/已支付）"),
                ("✅", "确认与锁定", "确认后的分润期间不可修改，确保审计合规"),
            ]
        ),
        "/approvals": (
            [
                ("📝", "飞书审批集成", "对接飞书审批工作流，消息通知实时推送"),
                ("🔀", "Shadow / Live 模式", "支持影子模式和实时模式的灰度发布"),
                ("⏪", "一键回滚", "异常情况下快速回滚到上一版本配置"),
                ("🔒", "熔断保护", "自动熔断机制，防止错误策略持续执行"),
            ]
        ),
    }

    features = module_features.get(
        path,
        [
            ("📊", "数据总览", "实时数据监控与分析"),
            ("⚙️", "配置管理", "灵活的系统参数配置"),
            ("📈", "趋势分析", "多维度数据趋势展示"),
            ("🔔", "智能告警", "异常情况实时通知"),
        ],
    )

    feature_cards = "".join(
        f"""
        <div class="feature-card">
          <div class="feature-icon">{icon}</div>
          <h3>{html.escape(card_title)}</h3>
          <p>{html.escape(desc)}</p>
        </div>
        """
        for icon, card_title, desc in features
    )

    body = f"""
    <div class="module-hero">
      <h1>{html.escape(title)}</h1>
      <p>{html.escape(summary)}</p>
    </div>
    <div class="feature-grid">
      {feature_cards}
    </div>
    <div class="panel">
      <div class="panel-header">
        <h2>功能说明</h2>
        <span class="panel-desc">模块功能正在开发中</span>
      </div>
      <div style="text-align:center; padding:40px 20px; color:var(--color-text-muted);">
        <div style="font-size:3rem; margin-bottom:12px;">🚧</div>
        <div style="font-weight:600; color:var(--color-text-secondary); margin-bottom:4px;">{html.escape(title)}模块</div>
        <div style="font-size:0.88rem;">{html.escape(summary)}</div>
        <div style="margin-top:16px; font-size:0.82rem;">详细功能页面即将上线，敬请期待</div>
      </div>
    </div>
    """
    return _page_shell(
        title=title,
        active_path=path,
        header_title=title,
        header_meta=summary,
        badge_text="Module",
        body_content=body,
    )


def render_optimization_center(
    *,
    platform_roas: str,
    net_sales_roas: str,
    profit_roas: str,
    confidence: str,
    evidence: tuple[str, ...],
    action: str,
    before: str,
    after: str,
    execution_status: str,
) -> str:
    """渲染广告调优中心组件（嵌入在 Dashboard 或模块页面中）。"""
    items = "".join(f"<li>{html.escape(item)}</li>" for item in evidence)
    return f"""
    <section class="panel">
      <div class="panel-header"><h2>广告调优</h2></div>
      <dl class="kpi-details" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr));">
        <div class="kpi-detail"><dt>平台 ROAS</dt><dd class="number text-roas">{html.escape(platform_roas)}</dd></div>
        <div class="kpi-detail"><dt>净销售 ROAS</dt><dd class="number text-roas">{html.escape(net_sales_roas)}</dd></div>
        <div class="kpi-detail"><dt>利润 ROAS</dt><dd class="number text-roas">{html.escape(profit_roas)}</dd></div>
        <div class="kpi-detail"><dt>数据可信度</dt><dd class="number">{html.escape(confidence)}</dd></div>
      </dl>
      <div style="margin-top:var(--space-lg);">
        <h3 style="font-size:0.95rem;font-weight:700;margin-bottom:var(--space-sm);color:var(--color-text);">建议与证据</h3>
        <p style="color:var(--color-text-secondary);margin-bottom:var(--space-md);">
          <strong>{html.escape(action)}</strong>：{html.escape(before)} → {html.escape(after)}
        </p>
        <ul style="margin:0;padding-left:20px;color:var(--color-text-secondary);">
          {items}
        </ul>
        <p style="margin-top:var(--space-md);color:var(--color-text-muted);font-size:0.85rem;">
          执行状态：<strong style="color:var(--color-primary);">{html.escape(execution_status)}</strong>
        </p>
      </div>
    </section>
    """


# ===== 模块页面补充样式 =====
# 以下样式用于库存与分润模块页面，补充 _page_styles() 中缺失的 KPI 卡片网格、
# 协议信息卡片、空状态提示等组件样式，保持与 Dashboard 主页面风格一致。

_MODULE_PAGE_STYLES = """
    /* KPI 卡片网格：与 Dashboard 的 .kpi-grid 视觉保持一致 */
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: var(--space-lg);
      margin-top: var(--space-lg);
    }
    .kpi-card {
      position: relative;
      background: var(--color-surface);
      border: 1px solid var(--color-border);
      border-radius: var(--radius-xl);
      padding: var(--space-lg);
      box-shadow: var(--shadow-md);
      transition: transform var(--transition-normal), box-shadow var(--transition-normal);
      overflow: hidden;
    }
    .kpi-card::before {
      content: "";
      position: absolute;
      top: 0; left: 0; right: 0;
      height: 4px;
      background: linear-gradient(90deg, var(--color-primary), var(--color-primary-light));
    }
    .kpi-card:hover {
      transform: translateY(-4px);
      box-shadow: var(--shadow-card-hover);
    }
    .kpi-card .eyebrow {
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
    }
    .kpi-card .kpi-value {
      font-size: 2.2rem;
      font-weight: 800;
      letter-spacing: -0.03em;
      color: var(--color-primary);
      font-variant-numeric: tabular-nums;
      line-height: 1.1;
    }
    .kpi-card .kpi-label {
      margin-top: 6px;
      font-size: 0.85rem;
      color: var(--color-text-muted);
      font-weight: 500;
    }
    /* 协议信息卡片：水平展示合伙人比例 */
    .agreement-card {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: var(--space-md);
    }
    .agreement-field {
      padding: var(--space-md);
      background: var(--color-bg);
      border-radius: var(--radius-md);
      border: 1px solid var(--color-border-light);
    }
    .agreement-field .field-label {
      color: var(--color-text-muted);
      font-size: 0.78rem;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin-bottom: 6px;
    }
    .agreement-field .field-value {
      font-size: 1.05rem;
      font-weight: 700;
      color: var(--color-text);
      font-variant-numeric: tabular-nums;
    }
    /* 净利润明细：分行展示收入与扣减项 */
    .breakdown-list {
      list-style: none;
      margin: 0;
      padding: 0;
    }
    .breakdown-list li {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 10px 14px;
      border-radius: var(--radius-md);
      background: var(--color-bg);
      margin-bottom: 8px;
      border: 1px solid var(--color-border-light);
    }
    .breakdown-list li:last-child {
      margin-bottom: 0;
      background: var(--color-primary-bg);
      border-color: rgba(79, 70, 229, 0.2);
    }
    .breakdown-list .breakdown-label {
      color: var(--color-text-secondary);
      font-size: 0.88rem;
      font-weight: 500;
    }
    .breakdown-list li:last-child .breakdown-label {
      color: var(--color-primary-dark);
      font-weight: 700;
    }
    .breakdown-list .breakdown-value {
      font-family: var(--font-mono);
      font-variant-numeric: tabular-nums;
      font-weight: 700;
      color: var(--color-text);
    }
    /* 状态标签 */
    .status-tag {
      display: inline-flex;
      align-items: center;
      padding: 3px 10px;
      border-radius: 999px;
      font-size: 0.75rem;
      font-weight: 600;
      letter-spacing: 0.02em;
    }
    .status-tag.status-draft {
      background: var(--color-accent-bg);
      color: #B45309;
    }
    .status-tag.status-confirmed,
    .status-tag.status-paid,
    .status-tag.status-success {
      background: var(--color-success-bg);
      color: var(--color-success);
    }
    .status-tag.status-planned {
      background: var(--color-primary-bg);
      color: var(--color-primary);
    }
    .status-tag.status-reversed {
      background: var(--color-danger-bg);
      color: var(--color-danger);
    }
    /* 空状态：用于表格中无数据时的占位提示 */
    td.empty-state {
      text-align: center;
      padding: 28px 16px;
      color: var(--color-text-muted);
      font-size: 0.9rem;
    }
    @media (max-width: 700px) {
      .kpi-card .kpi-value { font-size: 1.8rem; }
    }
    """


def _format_decimal(value, places: int = 2) -> str:
    """格式化数字为指定小数位数的字符串。

    与 Dashboard 主页面（app.render_dashboard）的格式化逻辑保持一致，
    None 或非法值返回 "N/A" 占位文本。

    参数:
        value: 待格式化的数值，支持 Decimal/int/float/str/None
        places: 保留的小数位数，默认 2 位

    返回:
        格式化后的字符串，如 "1.50" 或 "N/A"
    """
    if value is None:
        return "N/A"
    amount = value if isinstance(value, Decimal) else _to_decimal(value)
    return f"{amount:.{places}f}"


def _format_cny(value) -> str:
    """将金额格式化为人民币展示字符串，带 ¥ 前缀和正负号。

    与 Dashboard 主页面（app.render_dashboard）的格式化逻辑保持一致，
    None 返回 "待补数据"，负数显示为 "-¥123.45"。

    参数:
        value: 待格式化的金额，支持 Decimal/int/float/str/None

    返回:
        人民币展示字符串，如 "¥1234.56" 或 "-¥123.45"
    """
    if value is None:
        return "待补数据"
    amount = value if isinstance(value, Decimal) else _to_decimal(value)
    sign = "-" if amount < 0 else ""
    return f"{sign}¥{abs(amount):.2f}"


def _format_amount(value) -> str:
    """将金额值格式化为带 2 位小数的字符串。

    兼容 Decimal、int、float、str 与 None 多种输入类型，统一输出人民币
    展示用的两位小数字符串；None 或非法值返回占位文本。
    """
    if value is None:
        return "—"
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return "—"
    return f"{amount:.2f}"


def _to_decimal(value) -> Decimal:
    """将任意值转换为 Decimal，转换失败时返回 0。

    用于需要做数值比较（如判断净利润正负）的场景，避免抛出异常中断渲染。
    """
    if value is None:
        return Decimal(0)
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)


def _format_ratio(value) -> str:
    """将分润比例（0~1 之间的小数）格式化为百分比字符串。

    输入可为 Decimal 或字符串形式的数值，例如 "0.6" 输出 "60%"；
    None 或非法值返回占位文本，避免把无效输入误显示为 0%。
    """
    if value is None:
        return "—"
    try:
        ratio = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return "—"
    return f"{(ratio * 100):.0f}%"


def _truncate_uuid(value) -> str:
    """截断 UUID 字符串，仅保留前 8 位以便页面展示。"""
    if not value:
        return "—"
    text = str(value)
    return text[:8] if len(text) >= 8 else text


def _render_status_tag(status: str) -> str:
    """根据状态值渲染带颜色样式的状态标签。"""
    if not status:
        return '<span class="status-tag">—</span>'
    safe_status = html.escape(str(status))
    css_status = str(status).lower()
    return f'<span class="status-tag status-{css_status}">{safe_status}</span>'


def _render_evidence_form(
    csrf_token: str,
    *,
    return_to: str,
    targets: tuple[tuple[str, str, str], ...],
) -> str:
    token = html.escape(csrf_token, quote=True)
    safe_return = html.escape(return_to, quote=True)
    options = "".join(
        f'<option value="{html.escape(object_type + ":" + object_id, quote=True)}">'
        f"{html.escape(label)}</option>"
        for object_type, object_id, label in targets
    )
    disabled = "" if options else " disabled"
    if not options:
        options = '<option value="">暂无可关联记录</option>'
    return f"""
    <form method="post" action="/evidence" enctype="multipart/form-data" class="embedded-form">
      <div class="embedded-form-title">添加凭证</div>
      <input type="hidden" name="csrf_token" value="{token}">
      <input type="hidden" name="return_to" value="{safe_return}">
      <div class="form-grid">
        <div class="form-field"><label>关联记录</label><select name="target" required>{options}</select></div>
        <div class="form-field"><label>PDF / JPEG / PNG（最大 5 MiB）</label><input type="file" name="file" accept="application/pdf,image/jpeg,image/png" required></div>
        <div class="form-field"><label>说明</label><input name="note" maxlength="2000"></div>
      </div>
      <div class="form-actions"><button type="submit" class="btn"{disabled}>上传凭证</button></div>
    </form>
    """


def _render_evidence_links(evidence_ids) -> str:
    identifiers = tuple(evidence_ids or ())
    if not identifiers:
        return '<span class="status-tag">无</span>'
    return " ".join(
        f'<a href="/evidence/{html.escape(str(identifier), quote=True)}">凭证 {index}</a>'
        for index, identifier in enumerate(identifiers, start=1)
    )


def _render_inventory_write_forms(
    csrf_token: str,
    data_date: date,
    *,
    receipts: tuple[dict, ...] = (),
    movements: tuple[dict, ...] = (),
) -> str:
    token = html.escape(csrf_token, quote=True)
    selected_date = html.escape(data_date.isoformat(), quote=True)
    receipt_options = "".join(
        f'<option value="{html.escape(str(item["id"]), quote=True)}">'
        f'{html.escape(str(item["id"]))} · {html.escape(str(item["supplier"]))}</option>'
        for item in receipts
        if item["status"] == "confirmed"
    ) or '<option value="">暂无可冲销采购单</option>'
    movement_options = "".join(
        f'<option value="{html.escape(str(item["id"]), quote=True)}">'
        f'{html.escape(str(item["seller_sku"]))} · {html.escape(str(item["movement_type"]))} · {item["quantity_delta"]}</option>'
        for item in movements
        if item["movement_type"] in {"damage", "inventory_adjustment"}
        and not item["is_reversed"]
        and item["reversal_of"] is None
    ) or '<option value="">暂无可冲销人工流水</option>'
    evidence_targets = tuple(
        (
            "purchase_receipt",
            str(item["id"]),
            f'采购 {item["id"]} · {item["supplier"]}',
        )
        for item in receipts
    ) + tuple(
        (
            "inventory_movement",
            str(item["id"]),
            f'库存 {item["seller_sku"]} · {item["movement_type"]}',
        )
        for item in movements
        if item["source_type"] in {"manual", "manual_adjustment"}
    )
    return f"""
    <section class="form-section" aria-label="库存维护">
      <div class="form-section-title">库存维护</div>
      <div class="form-section-desc">在本页维护履约方式和采购入库；待补成本可在上方清单逐行录入。</div>
      <form method="post" action="/fulfillment" class="embedded-form">
        <div class="embedded-form-title">履约方式版本</div>
        <input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid">
          <div class="form-field"><label>平台</label><input name="platform" value="shopee" required></div>
          <div class="form-field"><label>店铺</label><input name="store" required></div>
          <div class="form-field"><label>Seller SKU</label><input name="seller_sku" required></div>
          <div class="form-field"><label>生效日期</label><input type="date" name="effective_date" value="{selected_date}" required></div>
          <div class="form-field"><label>履约模式</label><select name="mode"><option value="supplier_fulfilled">货盘代发</option><option value="stocked">自有备货</option></select></div>
          <div class="form-field"><label>供货状态</label><select name="supply_status"><option value="available">可供货</option><option value="paused">暂停供货</option></select></div>
          <div class="form-field"><label>备注</label><input name="note" placeholder="选填"></div>
        </div>
        <div class="form-actions"><button type="submit" class="btn">新增履约方式版本</button></div>
      </form>
      <form method="post" action="/purchases" class="embedded-form">
        <div class="embedded-form-title">采购入库</div>
        <input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid">
          <div class="form-field"><label>采购单号</label><input name="receipt_id" required></div>
          <div class="form-field"><label>供应商</label><input name="supplier" required></div>
          <div class="form-field"><label>入库日期</label><input type="date" name="received_on" value="{selected_date}" required></div>
          <div class="form-field"><label>Seller SKU</label><input name="seller_sku" required></div>
          <div class="form-field"><label>产品名称</label><input name="product_name"></div>
          <div class="form-field"><label>入库数量</label><input name="quantity" inputmode="numeric" required></div>
          <div class="form-field"><label>原币单位价</label><input name="unit_cost_original" inputmode="decimal" required></div>
          <div class="form-field"><label>币种</label><select name="currency"><option>CNY</option><option>THB</option><option>SGD</option></select></div>
          <div class="form-field"><label>物流单号</label><input name="tracking_number"></div>
          <div class="form-field"><label>到货状态</label><select name="arrival_status"><option value="received">已入库</option><option value="in_transit">运输中</option><option value="pending">待发货</option></select></div>
          <div class="form-field"><label>备注</label><input name="note"></div>
        </div>
        <div class="form-actions"><button type="submit" class="btn btn-success">采购入库</button></div>
      </form>
      <form method="post" action="/inventory/adjust" class="embedded-form">
        <div class="embedded-form-title">人工盘点调整</div>
        <input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid">
          <div class="form-field"><label>Seller SKU</label><input name="seller_sku" required></div>
          <div class="form-field"><label>数量变动（可正可负）</label><input name="quantity_delta" inputmode="numeric" required></div>
          <div class="form-field"><label>发生日期</label><input type="date" name="occurred_on" value="{selected_date}" required></div>
          <div class="form-field"><label>原因</label><input name="reason" required></div>
        </div>
        <div class="form-actions"><button type="submit" class="btn">保存调整</button></div>
      </form>
      <form method="post" action="/purchases/reverse" class="embedded-form">
        <div class="embedded-form-title">采购冲销</div>
        <input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid">
          <div class="form-field"><label>采购单</label><select name="receipt_id" required>{receipt_options}</select></div>
          <div class="form-field"><label>冲销日期</label><input type="date" name="occurred_on" value="{selected_date}" required></div>
          <div class="form-field"><label>原因</label><input name="reason" required></div>
        </div>
        <div class="form-actions"><button type="submit" class="btn btn-danger">冲销采购</button></div>
      </form>
      <form method="post" action="/inventory/movements/reverse" class="embedded-form">
        <div class="embedded-form-title">人工流水冲销</div>
        <input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid">
          <div class="form-field"><label>库存流水</label><select name="movement_id" required>{movement_options}</select></div>
          <div class="form-field"><label>冲销日期</label><input type="date" name="occurred_on" value="{selected_date}" required></div>
          <div class="form-field"><label>原因</label><input name="reason" required></div>
        </div>
        <div class="form-actions"><button type="submit" class="btn btn-danger">冲销流水</button></div>
      </form>
      {_render_evidence_form(csrf_token, return_to="/inventory", targets=evidence_targets)}
    </section>
    """


def _render_missing_cost_item(
    item: dict,
    *,
    index: int,
    csrf_token: str,
) -> str:
    prefix = f"sku-cost-{index}"
    seller_sku = html.escape(str(item["seller_sku"]))
    seller_sku_attr = html.escape(str(item["seller_sku"]), quote=True)
    platform = html.escape(str(item["platform"]))
    platform_attr = html.escape(str(item["platform"]), quote=True)
    store = html.escape(str(item["store"]))
    store_attr = html.escape(str(item["store"]), quote=True)
    effective_date = html.escape(
        str(item["first_missing_date"]), quote=True
    )
    last_order_date = html.escape(str(item["last_order_date"]))
    return f"""
    <details class="missing-cost-item">
      <summary class="missing-cost-summary">
        <span class="missing-cost-main">
          <strong class="missing-cost-sku">{seller_sku}</strong>
          <span class="missing-cost-context">{platform} · {store}</span>
        </span>
        <span class="missing-cost-metrics">
          <span><small>订单</small><b>{item['order_count']}</b></span>
          <span><small>缺成本件数</small><b>{item['missing_units']}</b></span>
          <span><small>最近订单</small><b>{last_order_date}</b></span>
        </span>
        <span class="missing-cost-action">补录成本</span>
      </summary>
      <form method="post" action="/sku-costs" class="missing-cost-editor">
        <input type="hidden" name="csrf_token" value="{csrf_token}">
        <input type="hidden" name="platform" value="{platform_attr}">
        <input type="hidden" name="store" value="{store_attr}">
        <input type="hidden" name="seller_sku" value="{seller_sku_attr}">
        <div class="form-field">
          <label for="{prefix}-effective-date">成本生效日期</label>
          <input id="{prefix}-effective-date" type="date" name="effective_date" value="{effective_date}" required>
        </div>
        <div class="form-field">
          <label for="{prefix}-unit-cost">单位成本 (CNY)</label>
          <input id="{prefix}-unit-cost" name="unit_cost_cny" inputmode="decimal" required>
        </div>
        <div class="form-field">
          <label for="{prefix}-note">备注</label>
          <input id="{prefix}-note" name="note" placeholder="选填">
        </div>
        <button type="submit" class="btn btn-success">保存成本</button>
      </form>
    </details>
    """


def _render_order_cost_gap_item(item: dict) -> str:
    """Render a read-only order-level cost coverage gap."""
    return f"""
    <article class="order-cost-gap-item">
      <div class="missing-cost-main">
        <strong class="missing-cost-sku">{html.escape(str(item['seller_sku']))}</strong>
        <span class="missing-cost-context">
          {html.escape(str(item['platform']))} · {html.escape(str(item['store']))}
          · 订单 {html.escape(str(item['order_id']))}
        </span>
      </div>
      <div class="missing-cost-metrics">
        <span><small>缺口类型</small><b>订单成本快照</b></span>
        <span><small>订单日期</small><b>{html.escape(str(item['order_date']))}</b></span>
        <span><small>缺成本件数</small><b>{item['missing_units']}</b></span>
      </div>
    </article>
    """


def render_inventory_page(
    database,
    data_date: date,
    csrf_token: str = "",
    notice: str = "",
    notice_level: str = "",
) -> str:
    """渲染 SKU 与库存模块页面。

    展示库存总览 KPI、待补 SKU 成本、采购入库记录、库存流水和库存余额，
    数据来源于 :class:`InventoryService` 提供的查询方法。

    参数:
        database: 数据库访问对象
        data_date: 数据日期，用于页面标题展示

    返回:
        完整的 HTML 页面字符串
    """
    service = InventoryService(database)

    # 拉取各模块数据
    missing_costs = service.list_missing_sku_costs()
    order_cost_gaps = service.list_order_cost_gaps()
    missing_cost_keys = {
        (item["platform"], item["store"], item["seller_sku"])
        for item in missing_costs
    }
    order_cost_gaps = tuple(
        item
        for item in order_cost_gaps
        if (item["platform"], item["store"], item["seller_sku"])
        not in missing_cost_keys
    )
    platform_sku_count = service.platform_sku_count()
    latest_platform_inventory = service.latest_platform_inventory()
    receipts = service.list_purchase_receipts()
    shipments = service.list_order_shipments()
    all_movements = service.list_inventory_movements()
    balances = service.list_inventory_balances()

    # 库存流水仅展示最近 50 条，避免页面过长
    movements = all_movements[:50]

    # KPI 卡片数据
    kpi_missing_count = len(missing_costs)
    kpi_receipt_count = len(receipts)
    kpi_movement_count = len(all_movements)
    safe_notice_level = (
        notice_level if notice_level in {"success", "warning"} else "warning"
    )
    notice_banner = (
        f'<div class="recalculation-notice {safe_notice_level}" '
        f'role="status">{html.escape(notice)}</div>'
        if notice
        else ""
    )

    # 渲染待补 SKU 成本紧凑清单
    order_gap_items = "".join(
        _render_order_cost_gap_item(item) for item in order_cost_gaps
    )
    if missing_costs:
        token = html.escape(csrf_token, quote=True)
        missing_items = "".join(
            _render_missing_cost_item(
                item,
                index=index,
                csrf_token=token,
            )
            for index, item in enumerate(missing_costs)
        )
    else:
        missing_items = ""
    if not order_gap_items and not missing_items:
        missing_items = '<div class="empty-state">暂无订单成本或商品单价缺口</div>'
    else:
        missing_items = order_gap_items + missing_items

    # 渲染采购入库记录表格行
    if receipts:
        receipt_rows = "".join(
            f"""
            <tr>
              <td class="number">{_truncate_uuid(item['id'])}</td>
              <td>{html.escape(str(item['supplier']))}</td>
              <td>{html.escape(str(item['received_on']))}</td>
              <td class="number">{item['line_count']}</td>
              <td class="number">{_format_amount(item['total_amount_cny'])}</td>
              <td>{_render_status_tag(item['status'])}</td>
              <td>{_render_evidence_links(item['evidence_ids'])}</td>
            </tr>
            """
            for item in receipts
        )
    else:
        receipt_rows = '<tr><td colspan="7" class="empty-state">暂无采购入库记录</td></tr>'

    if shipments:
        shipment_rows = "".join(
            f"""
            <tr>
              <td>{html.escape(str(item['order_date']))}</td>
              <td>{html.escape(str(item['order_id']))}</td>
              <td>{html.escape(str(item['product_name']))}</td>
              <td>{html.escape(str(item['seller_sku']))}</td>
              <td class="number">{item['quantity']}</td>
              <td class="number">{_format_amount(item['unit_cost_cny'])}</td>
              <td class="number">{_format_amount(item['total_cost_cny'])}</td>
              <td>{html.escape(str(item['tracking_number']))}</td>
              <td>{html.escape(str(item['logistics_status']))}</td>
            </tr>
            """
            for item in shipments
        )
    else:
        shipment_rows = '<tr><td colspan="9" class="empty-state">暂无订单出库记录</td></tr>'

    # 渲染库存流水表格行
    if movements:
        movement_rows = "".join(
            f"""
            <tr>
              <td>{html.escape(str(item['seller_sku']))}</td>
              <td>{html.escape(str(item['movement_type']))}</td>
              <td class="number{' text-profit-positive' if item['quantity_delta'] >= 0 else ' text-profit-negative'}">{'+' if item['quantity_delta'] >= 0 else ''}{item['quantity_delta']}</td>
              <td>{html.escape(str(item['occurred_on']))}</td>
              <td>{html.escape(str(item['source_type']))}</td>
              <td>{_render_evidence_links(item['evidence_ids'])}</td>
            </tr>
            """
            for item in movements
        )
    else:
        movement_rows = '<tr><td colspan="6" class="empty-state">暂无库存流水</td></tr>'

    # 渲染库存余额表格行
    if balances:
        balance_rows = "".join(
            f"""
            <tr>
              <td>{html.escape(str(item['seller_sku']))}</td>
              <td class="number{' text-profit-positive' if item['units'] > 0 else ''}">{item['units']}</td>
              <td>{html.escape(str(item['updated_at']))}</td>
            </tr>
            """
            for item in balances
        )
    else:
        balance_rows = '<tr><td colspan="3" class="empty-state">暂无库存余额</td></tr>'

    body = f"""
    <div class="module-hero">
      <h1>SKU 与库存</h1>
      <p>维护 SKU 成本版本、采购入库、库存流水与订单成本快照，确保利润计算精确可追溯。</p>
    </div>
    <style>{_MODULE_PAGE_STYLES}</style>
    <style>
      .recalculation-notice {{
        margin-bottom:var(--space-lg); padding:14px 18px;
        border:1px solid; border-radius:12px; line-height:1.6;
        font-weight:600;
      }}
      .recalculation-notice.success {{
        color:var(--color-success-dark); border-color:var(--color-success);
        background:var(--color-success-light);
      }}
      .recalculation-notice.warning {{
        color:#92400e; border-color:#f59e0b; background:#fffbeb;
      }}
      .missing-cost-list {{ display:grid; gap:12px; min-width:0; }}
      .missing-cost-item {{
        min-width:0; overflow:hidden;
        border:1px solid var(--color-border); border-radius:12px;
        background:var(--color-surface);
        transition:border-color 180ms ease, box-shadow 180ms ease;
      }}
      .missing-cost-item[open] {{
        border-color:var(--color-primary-light);
        box-shadow:var(--shadow-sm);
      }}
      .missing-cost-summary {{
        display:grid;
        grid-template-columns:minmax(0,1fr) minmax(360px,1.25fr) auto;
        align-items:center; gap:20px; min-width:0; padding:16px 18px;
        cursor:pointer; list-style:none; transition:background 180ms ease;
      }}
      .missing-cost-summary::-webkit-details-marker {{ display:none; }}
      .missing-cost-summary:hover {{ background:var(--color-surface-hover); }}
      .missing-cost-summary:focus-visible {{
        outline:3px solid var(--color-primary-light); outline-offset:-3px;
      }}
      .missing-cost-main {{ display:grid; gap:4px; min-width:0; }}
      .missing-cost-sku {{ overflow-wrap:anywhere; line-height:1.35; }}
      .missing-cost-context {{
        color:var(--color-text-muted); font-size:.82rem;
      }}
      .missing-cost-metrics {{
        display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px;
      }}
      .missing-cost-metrics span {{ display:grid; gap:2px; min-width:0; }}
      .missing-cost-metrics small {{
        color:var(--color-text-muted); font-size:.76rem;
      }}
      .missing-cost-metrics b {{
        color:var(--color-text); font-variant-numeric:tabular-nums;
        overflow-wrap:anywhere;
      }}
      .missing-cost-action {{
        color:var(--color-primary); font-weight:700; white-space:nowrap;
      }}
      .missing-cost-action::after {{
        content:"展开"; margin-left:8px; font-size:.76rem;
        color:var(--color-text-muted); font-weight:500;
      }}
      .missing-cost-item[open] .missing-cost-action::after {{ content:"收起"; }}
      .order-cost-gap-item {{
        display:grid; grid-template-columns:minmax(0,1fr) minmax(360px,1.25fr);
        align-items:center; gap:20px; padding:16px 18px;
        border:1px solid var(--color-border); border-radius:12px;
        background:var(--color-bg);
      }}
      .order-cost-gap-item .missing-cost-metrics {{ grid-template-columns:repeat(3,minmax(0,1fr)); }}
      .missing-cost-editor {{
        display:grid;
        grid-template-columns:minmax(150px,.8fr) minmax(150px,.8fr) minmax(180px,1.2fr) auto;
        align-items:end; gap:12px; padding:16px 18px 18px;
        border-top:1px solid var(--color-border-light);
        background:var(--color-bg);
      }}
      .missing-cost-editor .form-field {{ min-width:0; }}
      .missing-cost-editor input {{ width:100%; min-height:44px; }}
      .missing-cost-editor .btn {{ min-height:44px; }}
      @media (max-width:1024px) {{
        .missing-cost-summary {{ grid-template-columns:minmax(0,1fr) auto; }}
        .missing-cost-metrics {{ grid-column:1 / -1; }}
        .missing-cost-editor {{
          grid-template-columns:repeat(2,minmax(0,1fr));
        }}
      }}
      @media (max-width:768px) {{
        .missing-cost-summary, .missing-cost-editor {{
          grid-template-columns:minmax(0,1fr);
        }}
        .missing-cost-metrics {{
          grid-template-columns:repeat(3,minmax(0,1fr));
        }}
        .missing-cost-action {{ justify-self:start; }}
        .missing-cost-editor .btn {{ width:100%; }}
      }}
      @media (prefers-reduced-motion:reduce) {{
        .missing-cost-item, .missing-cost-summary {{ transition:none; }}
      }}
    </style>
    {notice_banner}
    <section class="kpi-grid" aria-label="库存总览">
      <article class="kpi-card">
        <span class="eyebrow">待补成本</span>
        <div class="kpi-value">{kpi_missing_count}</div>
        <div class="kpi-label">订单成本覆盖缺口</div>
        <div class="kpi-note">商品单价缺口 {len(missing_costs)} 个 SKU</div>
      </article>
      <article class="kpi-card">
        <span class="eyebrow">采购入库</span>
        <div class="kpi-value">{kpi_receipt_count}</div>
        <div class="kpi-label">采购入库笔数</div>
      </article>
      <article class="kpi-card">
        <span class="eyebrow">库存流水</span>
        <div class="kpi-value">{kpi_movement_count}</div>
        <div class="kpi-label">库存流水条数</div>
      </article>
      <article class="kpi-card">
        <span class="eyebrow">SKU 总数</span>
        <div class="kpi-value">{platform_sku_count}</div>
        <div class="kpi-label">平台 SKU 数</div>
        <div class="kpi-note">最新平台库存 {latest_platform_inventory['available_units']} 件</div>
      </article>
    </section>

    <!-- 操作栏：立即重算 -->
    <section class="panel" style="margin-bottom:var(--space-lg);">
      <div class="panel-header">
        <h2>利润重算</h2>
        <span class="panel-desc">按最新经营输入重新计算所有已有广告日期</span>
      </div>
      <div class="form-actions" style="padding:var(--space-md);">
        <form method="post" action="/inventory/recalculate" style="display:inline-flex;align-items:center;gap:var(--space-md);">
          <input type="hidden" name="csrf_token" value="{html.escape(csrf_token, quote=True)}">
          <input type="hidden" name="data_date" value="{html.escape(data_date.isoformat(), quote=True)}">
          <button type="submit" class="btn btn-success" style="min-height:44px;padding:0 24px;font-weight:600;">立即重算</button>
          <span style="color:var(--color-text-muted);font-size:.85rem;line-height:1.4;">
            重算不会修改原始采集数据，也不会把缺失的退款、成本等输入自动当作 0。
          </span>
        </form>
      </div>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>待补成本与覆盖缺口</h2>
        <span class="panel-desc">订单成本快照与商品单价分别统计；取消、退货订单不计入</span>
      </div>
      <div class="missing-cost-list">{missing_items}</div>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>采购入库记录</h2>
        <span class="panel-desc">登记采购入库单后自动更新库存流水</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>单号</th><th>供应商</th><th>入库日期</th>
              <th>明细数</th><th>金额(CNY)</th><th>状态</th><th>凭证</th>
            </tr>
          </thead>
          <tbody>{receipt_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>库存流水</h2>
        <span class="panel-desc">最近 50 条 SKU 出入库记录</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Seller SKU</th><th>流水类型</th><th>数量变动</th>
              <th>发生日期</th><th>来源类型</th><th>凭证</th>
            </tr>
          </thead>
          <tbody>{movement_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>订单出库明细</h2>
        <span class="panel-desc">订单成本快照与平台物流状态</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>日期</th><th>订单</th><th>产品</th><th>SKU</th>
          <th>数量</th><th>单位成本</th><th>总成本</th><th>物流单号</th><th>发货状态</th></tr></thead>
          <tbody>{shipment_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>库存余额</h2>
        <span class="panel-desc">手工台账 SKU {len(balances)} 个 · 所有人工出入库后的当前余额</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Seller SKU</th><th>库存数量</th><th>更新时间</th>
            </tr>
          </thead>
          <tbody>{balance_rows}</tbody>
        </table>
      </div>
    </section>

    {_render_inventory_write_forms(
        csrf_token,
        data_date,
        receipts=receipts,
        movements=all_movements,
    )}
    """

    return _page_shell(
        title="SKU与库存",
        active_path="/inventory",
        header_title="SKU与库存",
        header_meta=f"数据日期 {data_date.isoformat()} · 库存与采购管理",
        badge_text="Inventory",
        body_content=body,
    )


def _render_profit_closure_forms(
    csrf_token: str,
    data_date: date,
    periods: tuple[dict, ...],
    payments: tuple[dict, ...],
) -> str:
    token = html.escape(csrf_token, quote=True)
    selected_date = html.escape(data_date.isoformat(), quote=True)

    def period_options(status: str, empty: str) -> str:
        rendered = "".join(
            f'<option value="{html.escape(str(item["id"]), quote=True)}">'
            f'{html.escape(str(item["starts_on"]))} 至 {html.escape(str(item["ends_on"]))} · {html.escape(str(item["id"]))}</option>'
            for item in periods
            if item["status"] == status
        )
        return rendered or f'<option value="">{html.escape(empty)}</option>'

    draft_periods = period_options("draft", "暂无待确认期间")
    confirmed_periods = period_options("confirmed", "暂无已确认期间")
    reversed_payment_ids = {
        str(item["reversal_of"])
        for item in payments
        if item["reversal_of"] is not None
    }
    payment_options = "".join(
        f'<option value="{html.escape(str(item["id"]), quote=True)}">'
        f'{html.escape(str(item["partner"]))} · {html.escape(str(item["amount_cny"]))} · {html.escape(str(item["id"]))}</option>'
        for item in payments
        if item["status"] == "paid"
        and str(item["id"]) not in reversed_payment_ids
        and item["reversal_of"] is None
    ) or '<option value="">暂无可冲销付款</option>'
    evidence_targets = tuple(
        (
            "profit_period",
            str(item["id"]),
            f'期间 · {item["starts_on"]} 至 {item["ends_on"]}',
        )
        for item in periods
    ) + tuple(
        (
            "profit_payment",
            str(item["id"]),
            f'付款 · {item["partner"]} · {item["amount_cny"]}',
        )
        for item in payments
    )
    return f"""
    <section class="form-section" aria-label="分润闭环维护">
      <div class="form-section-title">分润闭环维护</div>
      <div class="form-section-desc">协议和期间按版本保留；付款与期间冲销必须说明原因。</div>
      <form method="post" action="/profit-agreements" class="embedded-form">
        <div class="embedded-form-title">新增协议版本</div><input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid"><div class="form-field"><label>生效日期</label><input type="date" name="effective_from" value="{selected_date}" required></div><div class="form-field"><label>洁云比例</label><input name="jieyun_share" value="0.50" required></div><div class="form-field"><label>苏姐比例</label><input name="sujie_share" value="0.50" required></div></div>
        <div class="form-actions"><button type="submit" class="btn">新增版本</button></div>
      </form>
      <form method="post" action="/profit-periods" class="embedded-form">
        <div class="embedded-form-title">生成分润草稿</div><input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid"><div class="form-field"><label>开始日期</label><input type="date" name="starts_on" value="{selected_date}" required></div><div class="form-field"><label>结束日期</label><input type="date" name="ends_on" value="{selected_date}" required></div></div>
        <div class="form-actions"><button type="submit" class="btn">生成草稿</button></div>
      </form>
      <form method="post" action="/profit-periods/confirm" class="embedded-form">
        <div class="embedded-form-title">确认分润期间</div><input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid"><div class="form-field"><label>草稿期间</label><select name="period_id" required>{draft_periods}</select></div></div>
        <div class="form-actions"><button type="submit" class="btn btn-success">确认期间</button></div>
      </form>
      <form method="post" action="/profit-periods/reverse" class="embedded-form">
        <div class="embedded-form-title">冲销分润期间</div><input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid"><div class="form-field"><label>已确认期间</label><select name="period_id" required>{confirmed_periods}</select></div><div class="form-field"><label>原因</label><input name="reason" required></div></div>
        <div class="form-actions"><button type="submit" class="btn btn-danger">冲销期间</button></div>
      </form>
      <form method="post" action="/profit-payments" class="embedded-form">
        <div class="embedded-form-title">记录分润付款</div><input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid"><div class="form-field"><label>期间</label><select name="period_id" required>{confirmed_periods}</select></div><div class="form-field"><label>合伙人</label><input name="partner" required></div><div class="form-field"><label>金额</label><input name="amount_cny" required></div><div class="form-field"><label>日期</label><input type="date" name="paid_on" value="{selected_date}" required></div><div class="form-field"><label>状态</label><select name="status"><option value="paid">已支付</option><option value="planned">计划</option></select></div><div class="form-field"><label>备注</label><input name="note"></div></div>
        <div class="form-actions"><button type="submit" class="btn">记录付款</button></div>
      </form>
      <form method="post" action="/profit-payments/reverse" class="embedded-form">
        <div class="embedded-form-title">冲销付款</div><input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid"><div class="form-field"><label>已支付记录</label><select name="payment_id" required>{payment_options}</select></div><div class="form-field"><label>冲销日期</label><input type="date" name="reversed_on" value="{selected_date}" required></div><div class="form-field"><label>原因</label><input name="reason" required></div></div>
        <div class="form-actions"><button type="submit" class="btn btn-danger">冲销付款</button></div>
      </form>
      {_render_evidence_form(csrf_token, return_to="/profit-sharing", targets=evidence_targets)}
    </section>
    """


def render_profit_sharing_page(
    database, data_date: date, csrf_token: str = ""
) -> str:
    """渲染合伙人分润模块页面。

    展示分润总览 KPI、当前协议、分润期间列表、净利润明细和支付记录，
    数据来源于 :class:`ProfitSharingService` 提供的查询方法。

    参数:
        database: 数据库访问对象
        data_date: 数据日期，用于计算当月净利润明细

    返回:
        完整的 HTML 页面字符串
    """
    service = ProfitSharingService(database)

    # 拉取各模块数据
    current_agreement = service.get_current_agreement()
    periods = service.list_profit_periods()
    period_details = {
        item["id"]: service.get_period_detail(item["id"]) for item in periods
    }
    payments = service.list_profit_payments()

    # 当月净利润明细：起始日期为当月第一天，结束日期为传入的数据日期
    month_start = data_date.replace(day=1)
    breakdown = service.calculate_net_profit_breakdown(
        starts_on=month_start,
        ends_on=data_date,
    )

    # KPI 卡片数据
    kpi_agreement_version = (
        f"v{current_agreement['version']}" if current_agreement else "—"
    )
    kpi_period_count = len(periods)
    kpi_pending_count = sum(1 for item in periods if item["status"] == "draft")
    # 已支付净额：已支付正记录与冲销负记录共同求和。
    paid_total = Decimal(0)
    for item in payments:
        if item["status"] in {"paid", "reversed"}:
            try:
                paid_total += Decimal(str(item["amount_cny"]))
            except (InvalidOperation, ValueError, TypeError):
                continue

    # 渲染当前分润协议卡片
    if current_agreement:
        # 解析 shares_json 得到合伙人比例字典
        try:
            shares = json.loads(current_agreement["shares_json"])
        except (json.JSONDecodeError, TypeError):
            shares = {}

        effective_to = current_agreement.get("effective_to") or "至今"
        # 合伙人比例展示为百分比，按合伙人姓名排序保证展示稳定
        share_items = "".join(
            f"""
            <div class="agreement-field">
              <div class="field-label">{html.escape(partner)}</div>
              <div class="field-value">{_format_ratio(ratio_text)}</div>
            </div>
            """
            for partner, ratio_text in sorted(shares.items())
        )

        agreement_section = f"""
        <div class="agreement-card">
          <div class="agreement-field">
            <div class="field-label">协议版本</div>
            <div class="field-value">v{html.escape(str(current_agreement['version']))}</div>
          </div>
          <div class="agreement-field">
            <div class="field-label">生效起始</div>
            <div class="field-value">{html.escape(str(current_agreement['effective_from']))}</div>
          </div>
          <div class="agreement-field">
            <div class="field-label">生效结束</div>
            <div class="field-value">{html.escape(str(effective_to))}</div>
          </div>
          {share_items}
        </div>
        """
    else:
        agreement_section = (
            '<div class="empty-state" style="padding:32px 16px;">'
            "暂无生效的分润协议，请先在记账对账页面新增协议版本"
            "</div>"
        )

    # 渲染分润期间列表表格行
    if periods:
        period_rows = "".join(
            f"""
            <tr>
              <td class="number">{_truncate_uuid(item['id'])}</td>
              <td>{html.escape(str(item['starts_on']))}</td>
              <td>{html.escape(str(item['ends_on']))}</td>
              <td class="number{' text-profit-positive' if _to_decimal(item['net_profit_cny']) >= 0 else ' text-profit-negative'}">{_format_amount(item['net_profit_cny'])}</td>
              <td class="number">{_format_amount(period_details[item['id']]['distributable_profit_cny'])}</td>
              <td>{_render_status_tag(period_details[item['id']]['display_status'])}</td>
              <td>{_render_evidence_links(item['evidence_ids'])}</td>
            </tr>
            <tr><td colspan="7">{
                ' · '.join(
                    f"{html.escape(detail['partner'])}: 应分 {_format_amount(detail['due_cny'])} / 已付 {_format_amount(detail['paid_cny'])} / 未付 {_format_amount(detail['outstanding_cny'])}"
                    for detail in period_details[item['id']]['allocations']
                ) or '暂无分配明细'
            }</td></tr>
            """
            for item in periods
        )
    else:
        period_rows = '<tr><td colspan="7" class="empty-state">暂无分润期间记录</td></tr>'

    # 渲染净利润明细面板：列出收入、各项成本与最终净利润
    breakdown_items = "".join(
        f"""
        <li>
          <span class="breakdown-label">{html.escape(label)}</span>
          <span class="breakdown-value">{_format_amount(breakdown.get(key))}</span>
        </li>
        """
        for key, label in (
            ("revenue", "收入"),
            ("cogs", "销售成本"),
            ("platform_fee", "平台费"),
            ("ad_spend", "广告费"),
            ("operating_expenses", "经营费用"),
            ("review_costs", "刷单成本"),
            ("net_profit", "净利润"),
        )
    )

    # 渲染支付记录表格行
    if payments:
        payment_rows = "".join(
            f"""
            <tr>
              <td class="number">{_truncate_uuid(item['period_id'])}</td>
              <td>{html.escape(str(item['partner']))}</td>
              <td class="number">{_format_amount(item['amount_cny'])}</td>
              <td>{html.escape(str(item['paid_on']))}</td>
              <td>{_render_status_tag(item['status'])}</td>
              <td>{_render_evidence_links(item['evidence_ids'])}</td>
            </tr>
            """
            for item in payments
        )
    else:
        payment_rows = '<tr><td colspan="6" class="empty-state">暂无分润支付记录</td></tr>'

    body = f"""
    <div class="module-hero">
      <h1>合伙人分润</h1>
      <p>按经营账生成利润期间并依据生效协议记录应分与实付，确保分润流程可审计。</p>
    </div>
    <style>{_MODULE_PAGE_STYLES}</style>
    <section class="kpi-grid" aria-label="分润总览">
      <article class="kpi-card">
        <span class="eyebrow">当前协议</span>
        <div class="kpi-value">{html.escape(kpi_agreement_version)}</div>
        <div class="kpi-label">当前分润协议版本</div>
      </article>
      <article class="kpi-card">
        <span class="eyebrow">分润期间</span>
        <div class="kpi-value">{kpi_period_count}</div>
        <div class="kpi-label">分润期间数</div>
      </article>
      <article class="kpi-card">
        <span class="eyebrow">待确认</span>
        <div class="kpi-value">{kpi_pending_count}</div>
        <div class="kpi-label">待确认期间数</div>
      </article>
      <article class="kpi-card">
        <span class="eyebrow">已支付</span>
        <div class="kpi-value">{_format_amount(paid_total)}</div>
        <div class="kpi-label">已支付总额 (CNY)</div>
      </article>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>当前分润协议</h2>
        <span class="panel-desc">当前生效的分润协议版本与合伙人分润比例</span>
      </div>
      {agreement_section}
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>分润期间列表</h2>
        <span class="panel-desc">所有分润期间记录，按起始日期降序</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>期间 ID</th><th>起始日期</th><th>结束日期</th>
              <th>净利润 (CNY)</th><th>可分配利润</th><th>状态</th><th>凭证</th>
            </tr>
          </thead>
          <tbody>{period_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>净利润明细</h2>
        <span class="panel-desc">{month_start.isoformat()} 至 {data_date.isoformat()} 当月收入与成本分解</span>
      </div>
      <ul class="breakdown-list">{breakdown_items}</ul>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>支付记录</h2>
        <span class="panel-desc">分润支付记录，按支付日期降序</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>期间 ID</th><th>合伙人</th><th>金额 (CNY)</th>
              <th>支付日期</th><th>状态</th><th>凭证</th>
            </tr>
          </thead>
          <tbody>{payment_rows}</tbody>
        </table>
      </div>
    </section>
    {_render_profit_closure_forms(
        csrf_token,
        data_date,
        periods,
        payments,
    )}
    """

    return _page_shell(
        title="合伙人分润",
        active_path="/profit-sharing",
        header_title="合伙人分润",
        header_meta=f"数据日期 {data_date.isoformat()} · 分润协议与支付管理",
        badge_text="Profit Sharing",
        body_content=body,
    )


def render_optimization_page(database: Database, data_date: date) -> str:
    """渲染广告调优中心页面。

    展示三种 ROAS 对比、异常告警面板、Campaign/SKU 盈亏排行、策略建议详情
    以及审批与执行状态汇总，辅助决策者快速识别异常并执行优化策略。

    数据来源：
      - ``ReportReadModel.daily()`` 提供平台汇总、SKU 表现、告警与建议
      - ``ReportReadModel.dashboard()`` 提供审批与执行计数

    参数:
        database: 数据库访问对象
        data_date: 数据日期，用于查询当日报表数据

    返回:
        完整的 HTML 页面字符串
    """
    read_model = ReportReadModel(database)
    # 当日数据：平台汇总、SKU 表现、告警、建议
    snapshot = read_model.daily(data_date)
    # Dashboard 数据：审批和执行计数
    dashboard_snapshot = read_model.dashboard(data_date)
    metrics_ready = snapshot.capabilities.get("platform_metrics") == "ready"
    financial_ready = snapshot.capabilities.get("verified_profit") == "ready"
    strategy_ready = (
        snapshot.capabilities.get("inventory_safe_strategy") == "ready"
    )
    missing_profit_inputs = tuple(
        sorted(
            {
                input_name
                for source in snapshot.profit_sources
                for input_name in source.missing_inputs
            }
        )
    )
    missing_profit_label = "、".join(
        PROFIT_INPUT_LABELS.get(name, name) for name in missing_profit_inputs
    ) or "利润输入"

    # ===== 计算 ROAS 对比指标 =====
    total_spend = sum((p.spend for p in snapshot.platforms), Decimal(0))
    total_gmv = sum((p.gmv for p in snapshot.platforms), Decimal(0))
    total_ad_spend_cny = sum(
        (p.ad_spend_cny for p in snapshot.platforms if p.ad_spend_cny is not None),
        Decimal(0),
    )
    total_attributed_sales_cny = sum(
        (
            p.attributed_sales_cny
            for p in snapshot.platforms
            if p.attributed_sales_cny is not None
        ),
        Decimal(0),
    )
    total_net_profit = sum(
        (p.net_profit for p in snapshot.platforms if p.net_profit is not None),
        Decimal(0),
    )

    # 平台 ROAS = GMV / 消耗（原币维度）
    platform_roas = (
        _format_decimal(None if total_spend == 0 else total_gmv / total_spend)
        if metrics_ready
        else "待补数据"
    )
    # 净销售 ROAS = 净销售额 / 广告消耗（CNY 维度）
    net_sales_roas = (
        _format_decimal(
            None
            if total_ad_spend_cny == 0
            else total_attributed_sales_cny / total_ad_spend_cny
        )
        if financial_ready
        else "待补数据"
    )
    # 利润 ROAS = 净利润 / 广告消耗（CNY 维度）
    profit_roas = (
        _format_decimal(
            None
            if total_ad_spend_cny == 0
            else total_net_profit / total_ad_spend_cny
        )
        if financial_ready
        else "待补数据"
    )
    roas_gate_note = (
        "ROAS 数据门禁：财务输入已就绪"
        if financial_ready
        else f"ROAS 数据门禁：待补 {missing_profit_label}"
    )

    # ===== 异常告警：按 severity 优先级排序 =====
    severity_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    sorted_alerts = sorted(
        snapshot.alerts,
        key=lambda a: severity_order.get(
            str(a.get("severity", "")).lower(), 99
        ),
    )
    if sorted_alerts:
        alert_items = "".join(
            f"""<li>
              <strong class="severity-{html.escape(str(a.get('severity', '')).lower())}">{html.escape(str(a.get('severity', '')))}</strong>
              <span>{html.escape(str(a.get('message', '')))}</span>
            </li>"""
            for a in sorted_alerts
        )
    else:
        alert_items = '<li class="empty-row">暂无异常告警</li>'

    # ===== Campaign/SKU 盈亏排行：按净利润降序（盈利在前，亏损在后，None 在最后）=====
    sorted_skus = sorted(
        snapshot.sku_performance,
        key=lambda s: (s.net_profit is None, -_to_decimal(s.net_profit)),
    )
    if sorted_skus:
        sku_rows = "".join(
            f"""<tr>
              <td>{html.escape(item.platform.title())}</td>
              <td>{html.escape(item.store)}</td>
              <td>{html.escape(item.sku_id)}</td>
              <td>{html.escape(item.campaign_id)}</td>
              <td class="number text-roas">{_format_decimal(item.roas)}</td>
              <td class="number{' text-profit-positive' if item.net_profit and item.net_profit > 0 else ' text-profit-negative' if item.net_profit and item.net_profit < 0 else ''}">{_format_cny(item.net_profit)}</td>
            </tr>"""
            for item in sorted_skus
        )
    else:
        sku_rows = '<tr><td colspan="6" class="empty-state">暂无数据</td></tr>'

    # ===== 策略建议详情 =====
    if not strategy_ready:
        strategy_missing = (
            missing_profit_label
            if not financial_ready
            else "平台库存快照"
        )
        rec_items = (
            '<li class="empty-row">策略建议待补数据：'
            f"{html.escape(strategy_missing)}</li>"
        )
    elif snapshot.recommendations:
        rec_items = "".join(
            f"""<li>
              <strong class="severity-info">{html.escape(str(r.get('action', '')))}</strong>
              <span>{html.escape(str(r.get('platform', '')))}/{html.escape(str(r.get('campaign_id', '')))} — {html.escape(str(r.get('reason', '')))}</span>
            </li>"""
            for r in snapshot.recommendations
        )
    else:
        rec_items = '<li class="empty-row">暂无策略建议</li>'

    # ===== 审批与执行状态汇总 =====
    approval_summary = ", ".join(
        f"{html.escape(status)}: {count}"
        for status, count in sorted(dashboard_snapshot.approval_counts.items())
    ) or "暂无审批"
    execution_summary = ", ".join(
        f"{html.escape(status)}: {count}"
        for status, count in sorted(dashboard_snapshot.execution_counts.items())
    ) or "暂无执行"

    body = f"""
    <div class="module-hero">
      <h1>广告调优中心</h1>
      <p>三种 ROAS 对比、异常证据分析、策略建议引擎与审批执行审计</p>
    </div>

    <section class="panel">
      <div class="panel-header">
        <h2>ROAS 对比</h2>
        <span class="panel-desc">基于平台、净销售、利润三层视角</span>
      </div>
      <dl class="kpi-details" style="grid-template-columns:repeat(3,1fr);">
        <div class="kpi-detail"><dt>平台 ROAS</dt><dd class="number text-roas">{platform_roas}</dd></div>
        <div class="kpi-detail"><dt>净销售 ROAS</dt><dd class="number text-roas">{net_sales_roas}</dd></div>
        <div class="kpi-detail"><dt>利润 ROAS</dt><dd class="number text-roas">{profit_roas}</dd></div>
      </dl>
      <p class="form-section-desc" style="margin-top:var(--space-md);">{html.escape(roas_gate_note)}</p>
    </section>

    <section class="panel list-panel">
      <div class="panel-header">
        <h2>异常告警</h2>
        <span class="panel-desc">按优先级排序</span>
      </div>
      <ul>{alert_items}</ul>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>Campaign/SKU 盈亏排行</h2>
        <span class="panel-desc">按净利润排序</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>平台</th><th>店铺</th><th>SKU</th><th>Campaign</th><th>ROAS</th><th>净利润</th></tr></thead>
          <tbody>{sku_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="panel list-panel">
      <div class="panel-header">
        <h2>策略建议</h2>
        <span class="panel-desc">基于数据的智能优化建议</span>
      </div>
      <ul>{rec_items}</ul>
    </section>

    <section class="panel">
      <div class="panel-header"><h2>审批与执行状态</h2></div>
      <dl class="kpi-details" style="grid-template-columns:repeat(2,1fr);">
        <div class="kpi-detail"><dt>审批状态</dt><dd>{approval_summary}</dd></div>
        <div class="kpi-detail"><dt>执行状态</dt><dd>{execution_summary}</dd></div>
      </dl>
    </section>
    """

    return _page_shell(
        title="广告调优",
        active_path="/optimization",
        header_title="广告调优中心",
        header_meta=f"数据日期 {data_date.isoformat()} · 三种 ROAS · 策略建议",
        badge_text="Optimization",
        body_content=body,
    )


def _render_finance_closure_forms(
    database: Database, data_date: date, csrf_token: str
) -> str:
    ledger = LedgerService(database)
    balances = BalanceReconciliationService(database)
    token = html.escape(csrf_token, quote=True)
    selected_date = html.escape(data_date.isoformat(), quote=True)
    expenses = ledger.list_expenses()
    adjustments = ledger.list_adjustments()
    funding = ledger.list_ad_funding_entries()
    capital = ledger.list_capital_entries()
    withdrawals = ledger.list_withdrawal_entries()
    review_costs = ledger.list_review_order_costs()
    reconciliations = balances.list_reconciliations()
    snapshots = balances.list_snapshots(through=data_date)

    def options(items, *, value, label, empty):
        rendered = "".join(
            f'<option value="{html.escape(value(item), quote=True)}">'
            f"{html.escape(label(item))}</option>"
            for item in items
        )
        return rendered or f'<option value="">{html.escape(empty)}</option>'

    draft_options = options(
        (item for item in expenses if item["status"] == "draft"),
        value=lambda item: str(item["id"]),
        label=lambda item: f'{item["occurred_on"]} · {item["category"]} · {item["amount_cny"]}',
        empty="暂无费用草稿",
    )
    confirmed_expense_options = options(
        (item for item in expenses if item["status"] == "confirmed"),
        value=lambda item: str(item["id"]),
        label=lambda item: f'{item["occurred_on"]} · {item["category"]} · {item["amount_cny"]}',
        empty="暂无可冲销费用",
    )
    reversed_adjustments = {
        item.reversal_of
        for item in adjustments
        if item.reversal_of is not None
    }
    adjustment_options = options(
        (
            item
            for item in adjustments
            if item.status == "confirmed"
            and item.reversal_of is None
            and item.id not in reversed_adjustments
        ),
        value=lambda item: item.id,
        label=lambda item: f"{item.occurred_on} · {item.adjustment_type} · {item.amount_cny}",
        empty="暂无可冲销调整",
    )
    ledger_items = tuple(
        ("capital", item)
        for item in capital
        if item["status"] == "confirmed"
    ) + tuple(
        ("withdrawal", item)
        for item in withdrawals
        if item["status"] == "confirmed"
    ) + tuple(
        ("ad_funding", item)
        for item in funding
        if item["status"] == "confirmed"
    ) + tuple(
        ("review_order_cost", item)
        for item in review_costs
        if item["status"] == "confirmed"
    )
    ledger_options = options(
        ledger_items,
        value=lambda pair: f'{pair[0]}:{pair[1]["id"]}',
        label=lambda pair: f'{pair[0]} · {pair[1]["id"]}',
        empty="暂无可冲销账本记录",
    )
    open_options = options(
        (item for item in reconciliations if item.status == "open"),
        value=lambda item: item.id,
        label=lambda item: f"{item.data_date} · {item.id}",
        empty="暂无未解决差额",
    )
    balance_adjustments = options(
        (
            item
            for item in adjustments
            if item.adjustment_type in {"cash", "balance"}
            and item.status == "confirmed"
            and item.reversal_of is None
            and item.id not in reversed_adjustments
        ),
        value=lambda item: item.id,
        label=lambda item: f"{item.occurred_on} · {item.reason} · {item.amount_cny}",
        empty="暂无可用余额调整",
    )
    evidence_targets = tuple(
        ("expense", str(item["id"]), f'费用 · {item["category"]}')
        for item in expenses
    ) + tuple(
        (
            "financial_adjustment",
            item.id,
            f"调整 · {item.reason} · {item.amount_cny}",
        )
        for item in adjustments
    ) + tuple(
        ("ad_funding", str(item["id"]), f'广告资金 · {item["id"]}')
        for item in funding
    ) + tuple(
        ("balance_snapshot", item.id, f"余额 · {item.account_name}")
        for item in snapshots
    )
    adjustment_rows = "".join(
        f"<tr><td>{item.occurred_on.isoformat()}</td>"
        f"<td>{html.escape(item.adjustment_type)}</td>"
        f'<td class="number">{_format_cny(item.amount_cny)}</td>'
        f"<td>{html.escape(item.reason)}</td>"
        f"<td>{_render_status_tag(item.status)}</td>"
        f"<td>{_render_evidence_links(item.evidence_ids)}</td></tr>"
        for item in adjustments
    ) or '<tr><td colspan="6" class="empty-state">暂无财务调整</td></tr>'
    reconciliation_rows = "".join(
        f"<tr><td>{item.data_date.isoformat()}</td>"
        f"<td>{html.escape(item.snapshot_state)}</td>"
        f"<td>{_format_cny(item.ledger_cash_cny)}</td>"
        f"<td>{_format_cny(item.cash_difference_cny)}</td>"
        f"<td>{_format_cny(item.platform_difference_cny)}</td>"
        f"<td>{_format_cny(item.bank_difference_cny)}</td>"
        f"<td>{_render_status_tag(item.status)}</td></tr>"
        for item in reconciliations
    ) or '<tr><td colspan="7" class="empty-state">暂无余额对账</td></tr>'

    return f"""
    <section class="form-section" aria-label="财务闭环维护">
      <div class="form-section-title">财务闭环维护</div>
      <div class="form-section-desc">确认、调整和冲销均保留原事实；危险动作必须填写原因。</div>
      <div class="embedded-form">
        <div class="embedded-form-title">财务调整记录</div>
        <div class="table-wrap"><table><thead><tr><th>日期</th><th>类型</th><th>金额</th><th>原因</th><th>状态</th><th>凭证</th></tr></thead><tbody>{adjustment_rows}</tbody></table></div>
      </div>
      <div class="embedded-form">
        <div class="embedded-form-title">三方余额对账</div>
        <div class="table-wrap"><table><thead><tr><th>日期</th><th>快照</th><th>账本现金</th><th>现金差额</th><th>平台差额</th><th>银行差额</th><th>状态</th></tr></thead><tbody>{reconciliation_rows}</tbody></table></div>
      </div>
      <form method="post" action="/expenses" class="embedded-form">
        <div class="embedded-form-title">新增费用草稿</div><input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid">
          <div class="form-field"><label>日期</label><input type="date" name="occurred_on" value="{selected_date}" required></div>
          <div class="form-field"><label>类别</label><input name="category" required></div>
          <div class="form-field"><label>金额</label><input name="amount" inputmode="decimal" required></div>
          <div class="form-field"><label>币种</label><input name="currency" value="CNY" required></div>
          <div class="form-field"><label>汇率</label><input name="rate_to_cny" value="1" required></div>
          <div class="form-field"><label>付款人</label><input name="payer" required></div>
          <div class="form-field"><label>资金性质</label><select name="fund_nature"><option value="operating_expense">经营费用</option><option value="partner_advance">合伙人代垫</option></select></div>
          <div class="form-field"><label>备注</label><input name="note"></div>
        </div><input type="hidden" name="affects_profit" value="1">
        <div class="form-actions"><button type="submit" class="btn">保存草稿</button></div>
      </form>
      <form method="post" action="/expenses/confirm" class="embedded-form">
        <div class="embedded-form-title">确认费用</div><input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid"><div class="form-field"><label>费用草稿</label><select name="entry_id" required>{draft_options}</select></div></div>
        <div class="form-actions"><button type="submit" class="btn btn-success">确认并记现金</button></div>
      </form>
      <form method="post" action="/expenses/reverse" class="embedded-form">
        <div class="embedded-form-title">冲销费用</div><input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid"><div class="form-field"><label>已确认费用</label><select name="entry_id" required>{confirmed_expense_options}</select></div><div class="form-field"><label>原因</label><input name="reason" required></div></div>
        <div class="form-actions"><button type="submit" class="btn btn-danger">冲销费用</button></div>
      </form>
      <form method="post" action="/adjustments" class="embedded-form">
        <div class="embedded-form-title">新增财务调整</div><input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid"><div class="form-field"><label>类型</label><select name="adjustment_type"><option value="income">收入</option><option value="cash">现金</option><option value="balance">余额修复</option></select></div><div class="form-field"><label>金额（可正可负）</label><input name="amount_cny" required></div><div class="form-field"><label>日期</label><input type="date" name="occurred_on" value="{selected_date}" required></div><div class="form-field"><label>原因</label><input name="reason" required></div></div>
        <div class="form-actions"><button type="submit" class="btn">保存调整</button></div>
      </form>
      <form method="post" action="/adjustments/reverse" class="embedded-form">
        <div class="embedded-form-title">冲销财务调整</div><input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid"><div class="form-field"><label>调整记录</label><select name="entry_id" required>{adjustment_options}</select></div><div class="form-field"><label>日期</label><input type="date" name="occurred_on" value="{selected_date}" required></div><div class="form-field"><label>原因</label><input name="reason" required></div></div>
        <div class="form-actions"><button type="submit" class="btn btn-danger">冲销调整</button></div>
      </form>
      <form method="post" action="/ad-funding" class="embedded-form">
        <div class="embedded-form-title">广告资金登记</div><input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid"><div class="form-field"><label>平台</label><input name="platform" value="shopee" required></div><div class="form-field"><label>店铺</label><input name="store" required></div><div class="form-field"><label>类型</label><select name="entry_type"><option value="recharge">充值</option><option value="gift">赠送</option><option value="refund">退款</option></select></div><div class="form-field"><label>金额</label><input name="amount" required></div><div class="form-field"><label>日期</label><input type="date" name="occurred_on" value="{selected_date}" required></div><div class="form-field"><label>来源</label><input name="source" value="manual" required></div></div>
        <div class="form-actions"><button type="submit" class="btn">登记资金</button></div>
      </form>
      <form method="post" action="/ledger/reverse" class="embedded-form">
        <div class="embedded-form-title">账本记录冲销</div><input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid"><div class="form-field"><label>记录</label><select name="target" required>{ledger_options}</select></div><div class="form-field"><label>原因</label><input name="reason" required></div></div>
        <div class="form-actions"><button type="submit" class="btn btn-danger">冲销记录</button></div>
      </form>
      <form method="post" action="/balance-snapshots" class="embedded-form">
        <div class="embedded-form-title">记录余额快照</div><input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid"><div class="form-field"><label>账户类型</label><select name="account_type"><option value="cash">现金</option><option value="platform">平台</option><option value="bank">银行</option></select></div><div class="form-field"><label>账户名称</label><input name="account_name" required></div><div class="form-field"><label>余额</label><input name="amount_cny" required></div><div class="form-field"><label>日期</label><input type="date" name="snapshot_date" value="{selected_date}" required></div><div class="form-field"><label>来源</label><input name="source" value="manual" required></div><div class="form-field"><label>说明</label><input name="note"></div></div>
        <div class="form-actions"><button type="submit" class="btn">保存快照</button></div>
      </form>
      <form method="post" action="/balance-reconciliations" class="embedded-form">
        <div class="embedded-form-title">生成三方对账</div><input type="hidden" name="csrf_token" value="{token}"><input type="hidden" name="data_date" value="{selected_date}">
        <div class="form-actions"><button type="submit" class="btn">计算差额</button></div>
      </form>
      <form method="post" action="/balance-reconciliations/resolve" class="embedded-form">
        <div class="embedded-form-title">关联差额修复</div><input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid"><div class="form-field"><label>未解决对账</label><select name="reconciliation_id" required>{open_options}</select></div><div class="form-field"><label>余额调整</label><select name="adjustment_id" required>{balance_adjustments}</select></div></div>
        <div class="form-actions"><button type="submit" class="btn btn-success">标记已解决</button></div>
      </form>
      {_render_evidence_form(csrf_token, return_to="/ad-funds", targets=evidence_targets)}
    </section>
    """


def render_ad_funds_page(
    database: Database, data_date: date, csrf_token: str = ""
) -> str:
    """渲染收入与广告资金页面。

    展示资金总览 KPI、收入汇总、广告充值记录、广告消耗明细和费用列表，
    帮助决策者掌握平台收入与广告资金的流向和对账情况。

    数据来源：
      - ``ReportReadModel.daily()`` 提供平台汇总数据
      - ``LedgerService.list_ad_funding_entries()`` 提供当日广告充值记录
      - ``LedgerService.list_expenses()`` 提供当月已确认费用列表

    参数:
        database: 数据库访问对象
        data_date: 数据日期，用于查询当日平台数据和广告充值记录

    返回:
        完整的 HTML 页面字符串
    """
    read_model = ReportReadModel(database)
    ledger = LedgerService(database)

    # 当日平台汇总数据
    snapshot = read_model.daily(data_date)
    # 当日广告充值记录
    ad_funding_entries = ledger.list_ad_funding_entries(
        start_date=data_date, end_date=data_date
    )
    # 当月已确认费用列表，取最近 20 条
    month_start = data_date.replace(day=1)
    expenses = ledger.list_expenses(status="confirmed", start_date=month_start)[:20]

    # ===== 资金总览 KPI =====
    total_gmv = sum((p.gmv for p in snapshot.platforms), Decimal(0))
    total_spend = sum((p.spend for p in snapshot.platforms), Decimal(0))
    # 当日广告充值总额（仅统计 recharge 类型）
    total_recharge = sum(
        (
            e["amount_cny"]
            for e in ad_funding_entries
            if e["entry_type"] == "recharge"
        ),
        Decimal(0),
    )

    # ===== 收入汇总表格行 =====
    if snapshot.platforms:
        revenue_rows = "".join(
            f"""<tr>
              <td>{html.escape(p.platform.title())}</td>
              <td class="number">{_format_decimal(p.gmv)}</td>
              <td class="number">{_format_decimal(p.attributed_sales_cny)}</td>
              <td class="number{' text-profit-positive' if p.net_profit and p.net_profit > 0 else ' text-profit-negative' if p.net_profit and p.net_profit < 0 else ''}">{_format_cny(p.net_profit)}</td>
            </tr>"""
            for p in snapshot.platforms
        )
    else:
        revenue_rows = '<tr><td colspan="4" class="empty-state">暂无数据</td></tr>'

    # ===== 广告充值记录表格行 =====
    if ad_funding_entries:
        funding_rows = "".join(
            f"""<tr>
              <td>{html.escape(e['platform'])}</td>
              <td>{html.escape(e['store'])}</td>
              <td>{html.escape(e['entry_type'])}</td>
              <td class="number">{_format_cny(e['amount_cny'])}</td>
              <td>{e['occurred_on'].isoformat()}</td>
              <td>{html.escape(e['source'])}</td>
              <td>{html.escape(e['status'])}</td>
              <td>{_render_evidence_links(e['evidence_ids'])}</td>
            </tr>"""
            for e in ad_funding_entries
        )
    else:
        funding_rows = '<tr><td colspan="8" class="empty-state">暂无数据</td></tr>'

    # ===== 广告消耗明细表格行 =====
    if snapshot.platforms:
        spend_rows = "".join(
            f"""<tr>
              <td>{html.escape(p.platform.title())}</td>
              <td class="number">{_format_decimal(p.spend)}</td>
              <td class="number">{_format_decimal(p.ad_spend_cny)}</td>
              <td class="number text-roas">{_format_decimal(p.roas)}</td>
            </tr>"""
            for p in snapshot.platforms
        )
    else:
        spend_rows = '<tr><td colspan="4" class="empty-state">暂无数据</td></tr>'

    # ===== 费用列表表格行 =====
    if expenses:
        expense_rows = "".join(
            f"""<tr>
              <td>{e['occurred_on'].isoformat()}</td>
              <td>{html.escape(e['category'])}</td>
              <td class="number">{_format_cny(e['amount_cny'])}</td>
              <td>{html.escape(e['payer'])}</td>
              <td>{html.escape(e['status'])}</td>
              <td>{_render_evidence_links(e['evidence_ids'])}</td>
            </tr>"""
            for e in expenses
        )
    else:
        expense_rows = '<tr><td colspan="6" class="empty-state">暂无数据</td></tr>'

    body = f"""
    <div class="module-hero">
      <h1>收入与广告资金</h1>
      <p>平台收入、广告充值和广告实际消耗分账展示</p>
    </div>

    <section class="panel">
      <div class="panel-header">
        <h2>资金总览</h2>
        <span class="panel-desc">今日关键指标</span>
      </div>
      <dl class="kpi-details" style="grid-template-columns:repeat(3,1fr);">
        <div class="kpi-detail"><dt>今日 GMV</dt><dd class="number">{_format_decimal(total_gmv)}</dd></div>
        <div class="kpi-detail"><dt>广告消耗</dt><dd class="number">{_format_decimal(total_spend)}</dd></div>
        <div class="kpi-detail"><dt>今日广告充值</dt><dd class="number">{_format_cny(total_recharge)}</dd></div>
      </dl>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>收入汇总</h2>
        <span class="panel-desc">按平台展示</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>平台</th><th>GMV</th><th>净销售</th><th>净利润</th></tr></thead>
          <tbody>{revenue_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>广告充值记录</h2>
        <span class="panel-desc">当日充值/赠送/退款</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>平台</th><th>店铺</th><th>类型</th><th>金额</th><th>日期</th><th>来源</th><th>状态</th><th>凭证</th></tr></thead>
          <tbody>{funding_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>广告消耗明细</h2>
        <span class="panel-desc">按平台展示</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>平台</th><th>消耗(原币)</th><th>消耗(CNY)</th><th>ROAS</th></tr></thead>
          <tbody>{spend_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>费用列表</h2>
        <span class="panel-desc">最近 20 条已确认费用</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>日期</th><th>类别</th><th>金额</th><th>付款人</th><th>状态</th><th>凭证</th></tr></thead>
          <tbody>{expense_rows}</tbody>
        </table>
      </div>
    </section>
    {_render_finance_closure_forms(database, data_date, csrf_token)}
    """

    return _page_shell(
        title="收入与广告资金",
        active_path="/ad-funds",
        header_title="收入与广告资金",
        header_meta=f"数据日期 {data_date.isoformat()} · 平台收入与广告资金管理",
        badge_text="Ad Funds",
        body_content=body,
    )


def render_strategy_rules_page(
    database: Database, data_date: date, *,
    csrf_token: str = "",
    shadow_results: str = "",
    shadow_error: str = "",
) -> str:
    """渲染策略规则版本管理页面，包含 Shadow 模式模拟面板。

    展示当前生效版本信息、历史版本列表、规则参数详情
    以及版本创建时间和创建人，并包含新增版本和关闭版本的表单；
    额外提供 Shadow 模式模拟区域，用于预览新规则参数的推荐结果。

    数据来源:
        - ``StrategyRuleService.get_current_version()`` 获取当前版本
        - ``StrategyRuleService.list_versions()`` 获取历史版本列表

    参数:
        database: 数据库访问对象
        data_date: 数据日期，用于页面标题展示
        csrf_token: CSRF 令牌，用于表单提交
        shadow_results: URL 传入的 Shadow 模拟结果 JSON 字符串
        shadow_error: URL 传入的 Shadow 模拟错误信息

    返回:
        完整的 HTML 页面字符串
    """
    service = StrategyRuleService(database)

    # 获取当前生效版本
    current = service.get_current_version()

    # 获取所有历史版本
    all_versions = service.list_versions()

    # KPI 卡片数据
    kpi_total_versions = len(all_versions)
    kpi_current_version = f"v{current['version']}" if current else "—"
    kpi_current_from = (
        current["effective_from"] if current else "—"
    )

    # ===== 渲染当前版本卡片 =====
    if current:
        effective_to = current.get("effective_to") or "至今"
        # 解析参数为展示用的键值对
        params = current.get("parameters", {})
        param_items = "".join(
            f"""
            <div class="agreement-field">
              <div class="field-label">{html.escape(str(key))}</div>
              <div class="field-value">{html.escape(str(value))}</div>
            </div>
            """
            for key, value in sorted(params.items())
        )

        current_section = f"""
        <div class="agreement-card">
          <div class="agreement-field">
            <div class="field-label">版本号</div>
            <div class="field-value">v{html.escape(str(current['version']))}</div>
          </div>
          <div class="agreement-field">
            <div class="field-label">生效起始</div>
            <div class="field-value">{html.escape(str(current['effective_from']))}</div>
          </div>
          <div class="agreement-field">
            <div class="field-label">生效结束</div>
            <div class="field-value">{html.escape(str(effective_to))}</div>
          </div>
          <div class="agreement-field">
            <div class="field-label">创建人</div>
            <div class="field-value">{html.escape(str(current['created_by']))}</div>
          </div>
          <div class="agreement-field">
            <div class="field-label">创建时间</div>
            <div class="field-value">{html.escape(str(current['created_at']))}</div>
          </div>
        </div>
        <div style="margin-top:var(--space-lg);">
          <div class="form-section-title" style="margin-bottom:var(--space-md);">规则参数详情</div>
          <div class="agreement-card">
            {param_items}
          </div>
        </div>
        """
    else:
        current_section = (
            '<div class="empty-state" style="padding:32px 16px;">'
            "暂无策略规则版本，请先创建初始版本"
            "</div>"
        )

    # ===== 渲染历史版本列表 =====
    token = html.escape(csrf_token, quote=True) if csrf_token else ""
    selected_date = html.escape(data_date.isoformat(), quote=True)
    if all_versions:
        version_rows = "".join(
            f"""
            <tr>
              <td class="number">v{html.escape(str(item['version']))}</td>
              <td>{html.escape(str(item['effective_from']))}</td>
              <td>{html.escape(str(item['effective_to'] or '至今'))}</td>
              <td>{_render_status_tag(
                  'current' if (current and item['id'] == current['id'])
                  else 'history'
              )}</td>
              <td>{html.escape(str(item['created_by']))}</td>
              <td>{html.escape(str(item['created_at']))}</td>
              <td>
                {_render_close_version_form(token, item['id']) if item['effective_to'] is None else ''}
              </td>
            </tr>
            """
            for item in all_versions
        )
    else:
        version_rows = (
            '<tr><td colspan="7" class="empty-state">暂无历史版本记录</td></tr>'
        )

    # ===== 渲染参数详情表格（展开显示各版本参数）=====
    if all_versions:
        param_detail_rows = "".join(
            _render_param_detail_row(item, current)
            for item in all_versions
        )
    else:
        param_detail_rows = (
            '<tr><td colspan="4" class="empty-state">暂无参数详情</td></tr>'
        )

    body = f"""
    <div class="module-hero">
      <h1>策略规则版本管理</h1>
      <p>管理广告投放策略的核心参数版本，支持按生效日期自动切换，确保策略变更可追溯。</p>
    </div>
    <style>{_MODULE_PAGE_STYLES}</style>

    <!-- 新增版本表单 -->
    <section class="form-section" aria-label="新增策略规则版本">
      <div class="form-section-title">新增版本</div>
      <div class="form-section-desc">创建新的策略规则版本，设置生效日期和规则参数。</div>
      <form method="post" action="/strategy-rules/create" class="embedded-form">
        <input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid">
          <div class="form-field">
            <label>生效日期</label>
            <input type="date" name="effective_from" value="{selected_date}" required>
          </div>
          <div class="form-field">
            <label>创建人</label>
            <input name="actor" placeholder="操作人姓名" required>
          </div>
          <div class="form-field" style="grid-column: 1 / -1;">
            <label>规则参数 (JSON 格式)</label>
            <textarea name="parameters" rows="6" placeholder='{{"max_refund_rate": "0.30", "min_inventory_days": "14"}}' required></textarea>
          </div>
        </div>
        <div class="form-actions">
          <button type="submit" class="btn btn-success">创建新版本</button>
        </div>
      </form>
    </section>

    <section class="kpi-grid" aria-label="策略规则总览">
      <article class="kpi-card">
        <span class="eyebrow">当前版本</span>
        <div class="kpi-value">{html.escape(kpi_current_version)}</div>
        <div class="kpi-label">当前生效的规则版本</div>
      </article>
      <article class="kpi-card">
        <span class="eyebrow">生效日期</span>
        <div class="kpi-value">{html.escape(str(kpi_current_from))}</div>
        <div class="kpi-label">当前版本生效起始日期</div>
      </article>
      <article class="kpi-card">
        <span class="eyebrow">历史版本</span>
        <div class="kpi-value">{kpi_total_versions}</div>
        <div class="kpi-label">版本总数（含当前版本）</div>
      </article>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>当前生效版本</h2>
        <span class="panel-desc">当前正在使用的策略规则参数</span>
      </div>
      {current_section}
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>历史版本列表</h2>
        <span class="panel-desc">所有版本按版本号降序排列</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>版本号</th>
              <th>生效起始</th>
              <th>生效结束</th>
              <th>状态</th>
              <th>创建人</th>
              <th>创建时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>{version_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>各版本参数对比</h2>
        <span class="panel-desc">展示每个版本的核心规则参数值</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>版本</th>
              <th>max_refund_rate</th>
              <th>min_inventory_days</th>
              <th>其他参数</th>
            </tr>
          </thead>
          <tbody>{param_detail_rows}</tbody>
        </table>
      </div>
    </section>

    <!-- Shadow 模式模拟面板 -->
    <section class="panel">
      <div class="panel-header">
        <h2>Shadow 模式模拟</h2>
        <span class="panel-desc">在影子模式下模拟新规则的推荐结果，不实际写入数据库</span>
      </div>
      <div class="panel-body">
        {_render_shadow_error(shadow_error) if shadow_error else ""}
        <form method="post" action="/strategy-rules/shadow" class="embedded-form">
          <input type="hidden" name="csrf_token" value="{token}">
          <div class="form-grid">
            <div class="form-field" style="grid-column: 1 / -1;">
              <label>模拟规则参数 (JSON 格式)</label>
              <textarea name="parameters" rows="6" placeholder='{{"max_refund_rate": "0.30", "min_inventory_days": "14"}}' required></textarea>
            </div>
          </div>
          <div class="form-actions">
            <button type="submit" class="btn btn-secondary">运行 Shadow 模拟</button>
          </div>
        </form>
        {_render_shadow_results_table(shadow_results) if shadow_results else ""}
      </div>
    </section>
    """

    return _page_shell(
        title="策略规则",
        active_path="/strategy-rules",
        header_title="策略规则版本管理",
        header_meta=(
            f"数据日期 {data_date.isoformat()} · "
            f"版本总数 {kpi_total_versions}"
        ),
        badge_text="Strategy Rules",
        body_content=body,
    )


def _render_param_detail_row(
    version: dict, current: dict | None
) -> str:
    """渲染单个版本的参数详情表格行。

    提取 max_refund_rate、min_inventory_days 两个核心参数
    并将其余参数合并展示。

    参数:
        version: 版本记录字典
        current: 当前生效版本字典（用于标记当前版本）

    返回:
        表格行 HTML 字符串
    """
    params = version.get("parameters", {})
    refund_rate = params.get("max_refund_rate", "—")
    min_days = params.get("min_inventory_days", "—")

    # 收集其他参数（排除已展示的核心参数）
    core_keys = {"max_refund_rate", "min_inventory_days"}
    other_params = {
        k: v for k, v in params.items() if k not in core_keys
    }
    other_text = (
        ", ".join(f"{k}={v}" for k, v in sorted(other_params.items()))
        if other_params
        else "—"
    )

    # 标记当前版本
    is_current = current is not None and version["id"] == current["id"]
    version_label = (
        f"v{version['version']} <span class=\"status-tag status-confirmed\""
        'style="margin-left:6px;">当前</span>'
        if is_current
        else f"v{version['version']}"
    )

    return f"""
    <tr>
      <td>{version_label}</td>
      <td class="number">{html.escape(str(refund_rate))}</td>
      <td class="number">{html.escape(str(min_days))}</td>
      <td>{html.escape(other_text)}</td>
    </tr>
    """


def _render_close_version_form(csrf_token: str, version_id: str) -> str:
    """渲染关闭版本表单。

    生成一个内联表单，包含隐藏的 CSRF token 和版本 ID，
    点击"关闭"按钮后提交到 /strategy-rules/close 路由。

    参数:
        csrf_token: CSRF 令牌
        version_id: 版本唯一标识

    返回:
        表单 HTML 字符串
    """
    safe_token = html.escape(csrf_token, quote=True)
    safe_id = html.escape(version_id, quote=True)
    return f"""
    <form method="post" action="/strategy-rules/close" style="display:inline;">
      <input type="hidden" name="csrf_token" value="{safe_token}">
      <input type="hidden" name="version_id" value="{safe_id}">
      <button type="submit" class="btn btn-sm btn-danger">关闭</button>
    </form>
    """


ACTION_LABELS = {
    "pause": "暂停",
    "reduce": "降低预算",
    "no_change": "不调整",
}


def _render_shadow_results_table(shadow_results_json: str) -> str:
    """渲染 Shadow 模拟结果表格。

    将 JSON 字符串解析为结果列表，按平台、店铺分组展示
    每条记录的模拟推荐动作。

    参数:
        shadow_results_json: Shadow 模拟结果的 JSON 字符串

    返回:
        结果表格 HTML 字符串；JSON 解析失败返回错误提示
    """
    try:
        results = json.loads(shadow_results_json)
    except (json.JSONDecodeError, TypeError):
        return '<div class="empty-state">Shadow 结果解析失败</div>'

    if not results:
        return '<div class="empty-state">Shadow 模拟无结果返回</div>'

    rows = "".join(
        f"""
        <tr>
          <td>{html.escape(str(item.get("platform", "")))}</td>
          <td>{html.escape(str(item.get("store", "")))}</td>
          <td>{html.escape(str(item.get("campaign_id", "")))}</td>
          <td>{html.escape(str(item.get("sku_id", "")))}</td>
          <td class="number">{html.escape(str(item.get("total_spend", "0")))}</td>
          <td class="number">{html.escape(str(item.get("total_gmv", "0")))}</td>
          <td class="number">{html.escape(str(item.get("total_orders", "0")))}</td>
          <td>{_render_shadow_action_tag(item.get("simulated_action", ""))}</td>
        </tr>
        """
        for item in results
    )

    return f"""
    <div style="margin-top:var(--space-lg);">
      <div class="form-section-title" style="margin-bottom:var(--space-md);">
        Shadow 模拟结果（共 {len(results)} 条）
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>平台</th>
              <th>店铺</th>
              <th>活动 ID</th>
              <th>SKU</th>
              <th>花费</th>
              <th>GMV</th>
              <th>订单数</th>
              <th>模拟动作</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </div>
    """


def _render_shadow_action_tag(action: str) -> str:
    """渲染 Shadow 模拟动作的状态标签。

    参数:
        action: 模拟动作 ("pause" / "reduce" / "no_change")

    返回:
        带颜色样式的状态标签 HTML
    """
    label = ACTION_LABELS.get(action, action)
    safe_label = html.escape(label)
    if action == "pause":
        return f'<span class="status-tag status-reversed">{safe_label}</span>'
    if action == "reduce":
        return f'<span class="status-tag status-draft">{safe_label}</span>'
    return f'<span class="status-tag status-confirmed">{safe_label}</span>'


def _render_shadow_error(error_message: str) -> str:
    """渲染 Shadow 模拟错误提示。

    参数:
        error_message: 错误信息字符串

    返回:
        错误提示 HTML 字符串
    """
    safe_msg = html.escape(error_message)
    return f"""
    <div class="empty-state" style="color:var(--color-danger);margin-bottom:var(--space-md);">
      ⚠ Shadow 模拟出错：{safe_msg}
    </div>
    """


def render_approvals_page(
    database: Database, data_date: date, *, csrf_token: str = ""
) -> str:
    """渲染审批管理页面。

    展示审批列表、创建审批表单、决策表单、执行审计面板、熔断时间线，
    数据来源于 approvals、recommendations、execution_audits、circuit_state 表。

    参数:
        database: 数据库访问对象
        data_date: 数据日期，用于页面标题展示
        csrf_token: CSRF 令牌，用于表单提交

    返回:
        完整的 HTML 页面字符串
    """
    token = html.escape(csrf_token, quote=True)

    # ===== 查询数据 =====
    with database.connect() as connection:
        # 1. 审批列表（JOIN recommendations）
        approval_rows = connection.execute(
            """
            SELECT a.id, a.recommendation_id, a.status, a.requested_at,
                   a.expires_at, a.decided_at, a.decided_by, a.decision_reason,
                   r.action, r.platform, r.campaign_id, r.sku_id, r.data_date,
                   r.reason AS rec_reason
            FROM approvals a
            JOIN recommendations r ON a.recommendation_id = r.id
            ORDER BY a.requested_at DESC
            LIMIT 100
            """
        ).fetchall()

        # 2. 待审批的推荐列表（用于创建审批表单的下拉选择）
        pending_recommendations = connection.execute(
            """
            SELECT id, platform, campaign_id, sku_id, action, reason,
                   data_date
            FROM recommendations
            WHERE requires_approval = 1 AND status = 'proposed'
            ORDER BY id DESC
            LIMIT 50
            """
        ).fetchall()

        # 3. 执行审计列表
        audit_rows = connection.execute(
            """
            SELECT id, approval_id, action, before_json, after_json, status,
                   error_code, error_message, created_at, finished_at
            FROM execution_audits
            ORDER BY created_at DESC
            LIMIT 50
            """
        ).fetchall()

        # 4. 熔断状态
        circuit_row = connection.execute(
            """
            SELECT is_open, reasons_json, opened_at, recovered_at,
                   recovered_by, recovery_reason
            FROM circuit_state WHERE id=1
            """
        ).fetchone()

    # ===== 审批列表表格 =====
    if approval_rows:
        approval_table_rows = "".join(
            f"""
            <tr>
              <td class="number">{_truncate_uuid(str(row['id']))}</td>
              <td>{html.escape(str(row['action']))}</td>
              <td>{html.escape(str(row['platform']))}</td>
              <td>{html.escape(str(row['campaign_id']))}</td>
              <td>{html.escape(str(row['sku_id']))}</td>
              <td>{_render_approval_status_tag(row['status'])}</td>
              <td>{html.escape(str(row['requested_at']))}</td>
              <td>{html.escape(str(row['expires_at']))}</td>
              <td>{html.escape(str(row['decided_by'] or '—'))}</td>
            </tr>
            """
            for row in approval_rows
        )
    else:
        approval_table_rows = (
            '<tr><td colspan="9" class="empty-state">暂无审批记录</td></tr>'
        )

    # ===== 创建审批表单的推荐选项 =====
    if pending_recommendations:
        rec_options = "".join(
            f"""
            <option value="{row['id']}">
              #{row['id']} · {html.escape(str(row['platform']))}/
              {html.escape(str(row['campaign_id']))} ·
              {html.escape(str(row['action']))}
            </option>
            """
            for row in pending_recommendations
        )
    else:
        rec_options = '<option value="">暂无可创建的审批</option>'

    rec_disabled = "" if pending_recommendations else " disabled"

    # ===== 执行审计表格 =====
    if audit_rows:
        audit_table_rows = "".join(
            f"""
            <tr>
              <td class="number">{_truncate_uuid(str(row['id']))}</td>
              <td class="number">{_truncate_uuid(str(row['approval_id']))}</td>
              <td>{html.escape(str(row['action']))}</td>
              <td>{_render_execution_status_tag(row['status'])}</td>
              <td>{html.escape(str(row['created_at']))}</td>
              <td>{html.escape(str(row['error_message'] or '—'))}</td>
            </tr>
            """
            for row in audit_rows
        )
    else:
        audit_table_rows = (
            '<tr><td colspan="6" class="empty-state">暂无执行审计记录</td></tr>'
        )

    # ===== 熔断状态面板 =====
    if circuit_row and circuit_row["is_open"]:
        circuit_status_label = "熔断已触发"
        circuit_status_css = "status-reversed"
        circuit_reasons = circuit_row["reasons_json"] or "[]"
        circuit_opened_at = html.escape(str(circuit_row["opened_at"] or "—"))
        circuit_recovery_info = ""
        if circuit_row["recovered_at"]:
            circuit_recovery_info = (
                f'<p style="margin-top:var(--space-sm);color:var(--color-success);">'
                f"已恢复于 {html.escape(str(circuit_row['recovered_at']))}"
                f" · 操作人: {html.escape(str(circuit_row['recovered_by'] or '—'))}"
                f"</p>"
            )
    elif circuit_row:
        circuit_status_label = "熔断未触发"
        circuit_status_css = "status-confirmed"
        circuit_reasons = "[]"
        circuit_opened_at = "—"
        circuit_recovery_info = ""
        if circuit_row["recovered_at"]:
            circuit_recovery_info = (
                f'<p style="margin-top:var(--space-sm);color:var(--color-success);">'
                f"已恢复于 {html.escape(str(circuit_row['recovered_at']))}"
                f" · 操作人: {html.escape(str(circuit_row['recovered_by'] or '—'))}"
                f"</p>"
            )
    else:
        circuit_status_label = "未知"
        circuit_status_css = ""
        circuit_reasons = "[]"
        circuit_opened_at = "—"
        circuit_recovery_info = ""

    body = f"""
    <div class="module-hero">
      <h1>审批执行</h1>
      <p>管理审批请求、执行审计与熔断保护，确保策略变更安全可控。</p>
    </div>
    <style>{_MODULE_PAGE_STYLES}</style>
    <style>
      /* 审批页面专用样式 */
      .approval-status-pending {{
        background: var(--color-accent-bg); color: #B45309;
      }}
      .approval-status-approved {{
        background: var(--color-success-bg); color: var(--color-success);
      }}
      .approval-status-rejected {{
        background: var(--color-danger-bg); color: var(--color-danger);
      }}
      .approval-status-expired {{
        background: var(--color-bg); color: var(--color-text-muted);
      }}
      .execution-status-success {{
        background: var(--color-success-bg); color: var(--color-success);
      }}
      .execution-status-failed {{
        background: var(--color-danger-bg); color: var(--color-danger);
      }}
      .execution-status-running {{
        background: var(--color-primary-bg); color: var(--color-primary);
      }}
      .circuit-panel {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: var(--space-lg);
      }}
      .circuit-stat {{
        padding: var(--space-md);
        background: var(--color-bg);
        border-radius: var(--radius-md);
        border: 1px solid var(--color-border-light);
      }}
      .circuit-stat .stat-label {{
        color: var(--color-text-muted);
        font-size: 0.78rem;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin-bottom: 4px;
      }}
      .circuit-stat .stat-value {{
        font-size: 1.05rem;
        font-weight: 700;
        color: var(--color-text);
        font-variant-numeric: tabular-nums;
      }}
    </style>

    <!-- ===== KPI 统计卡片 ===== -->
    <section class="kpi-grid" aria-label="审批总览">
      <article class="kpi-card">
        <span class="eyebrow">审批总数</span>
        <div class="kpi-value">{len(approval_rows)}</div>
        <div class="kpi-label">审批请求总数</div>
      </article>
      <article class="kpi-card">
        <span class="eyebrow">待审批</span>
        <div class="kpi-value">{sum(1 for r in approval_rows if r['status'] == 'pending')}</div>
        <div class="kpi-label">待处理的审批请求</div>
      </article>
      <article class="kpi-card">
        <span class="eyebrow">执行审计</span>
        <div class="kpi-value">{len(audit_rows)}</div>
        <div class="kpi-label">执行审计记录数</div>
      </article>
      <article class="kpi-card">
        <span class="eyebrow">熔断状态</span>
        <div class="kpi-value"><span class="status-tag {circuit_status_css}">{html.escape(circuit_status_label)}</span></div>
        <div class="kpi-label">当前熔断器状态</div>
      </article>
    </section>

    <!-- ===== 创建审批表单 ===== -->
    <section class="form-section" aria-label="创建审批请求">
      <div class="form-section-title">创建审批请求</div>
      <div class="form-section-desc">从待审批的推荐中创建审批请求，审批通过后自动执行策略变更。</div>
      <form method="post" action="/approvals/create" class="embedded-form">
        <input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid">
          <div class="form-field">
            <label>选择推荐</label>
            <select name="recommendation_id" required{rec_disabled}>
              {rec_options}
            </select>
          </div>
        </div>
        <div class="form-actions">
          <button type="submit" class="btn btn-success"{rec_disabled}>创建审批请求</button>
        </div>
      </form>
    </section>

    <!-- ===== 决策表单 ===== -->
    <section class="form-section" aria-label="审批决策">
      <div class="form-section-title">审批决策</div>
      <div class="form-section-desc">输入审批 ID 和决策令牌，批准或拒绝审批请求。</div>
      <form method="post" action="/approvals/decide" class="embedded-form">
        <input type="hidden" name="csrf_token" value="{token}">
        <div class="form-grid">
          <div class="form-field">
            <label>审批 ID</label>
            <input name="approval_id" placeholder="审批唯一标识" required>
          </div>
          <div class="form-field">
            <label>决策令牌</label>
            <input name="token" placeholder="创建审批时返回的令牌" required>
          </div>
          <div class="form-field">
            <label>决策</label>
            <select name="decision" required>
              <option value="approve">批准</option>
              <option value="reject">拒绝</option>
            </select>
          </div>
          <div class="form-field">
            <label>操作人</label>
            <input name="actor" placeholder="操作人姓名" required>
          </div>
          <div class="form-field" style="grid-column: 1 / -1;">
            <label>原因</label>
            <textarea name="reason" placeholder="决策原因（选填）"></textarea>
          </div>
        </div>
        <div class="form-actions">
          <button type="submit" class="btn">提交决策</button>
        </div>
      </form>
    </section>

    <!-- ===== 审批列表面板 ===== -->
    <section class="panel">
      <div class="panel-header">
        <h2>审批列表</h2>
        <span class="panel-desc">最近 100 条审批记录，按请求时间降序</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>审批 ID</th>
              <th>推荐动作</th>
              <th>平台</th>
              <th>Campaign</th>
              <th>SKU</th>
              <th>状态</th>
              <th>请求时间</th>
              <th>过期时间</th>
              <th>决策人</th>
            </tr>
          </thead>
          <tbody>{approval_table_rows}</tbody>
        </table>
      </div>
    </section>

    <!-- ===== 执行审计面板 ===== -->
    <section class="panel">
      <div class="panel-header">
        <h2>执行审计</h2>
        <span class="panel-desc">最近 50 条执行审计记录，按创建时间降序</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>审计 ID</th>
              <th>审批 ID</th>
              <th>操作</th>
              <th>状态</th>
              <th>创建时间</th>
              <th>错误信息</th>
            </tr>
          </thead>
          <tbody>{audit_table_rows}</tbody>
        </table>
      </div>
    </section>

    <!-- ===== 熔断状态面板 ===== -->
    <section class="panel">
      <div class="panel-header">
        <h2>熔断状态</h2>
        <span class="panel-desc">当前熔断器状态与触发历史</span>
      </div>
      <div class="circuit-panel">
        <div class="circuit-stat">
          <div class="stat-label">状态</div>
          <div class="stat-value"><span class="status-tag {circuit_status_css}">{html.escape(circuit_status_label)}</span></div>
        </div>
        <div class="circuit-stat">
          <div class="stat-label">触发时间</div>
          <div class="stat-value">{circuit_opened_at}</div>
        </div>
        <div class="circuit-stat">
          <div class="stat-label">触发原因</div>
          <div class="stat-value">{html.escape(circuit_reasons)}</div>
        </div>
      </div>
      {circuit_recovery_info}
    </section>
    """

    return _page_shell(
        title="审批执行",
        active_path="/approvals",
        header_title="审批执行",
        header_meta=(
            f"数据日期 {data_date.isoformat()} · "
            f"审批总数 {len(approval_rows)} · 执行审计 {len(audit_rows)}"
        ),
        badge_text="Approvals",
        body_content=body,
    )


def _render_approval_status_tag(status: str) -> str:
    """根据审批状态渲染带颜色样式的状态标签。"""
    css_map = {
        "pending": "approval-status-pending",
        "approved": "approval-status-approved",
        "rejected": "approval-status-rejected",
        "expired": "approval-status-expired",
    }
    css_class = css_map.get(status.lower(), "")
    safe_status = html.escape(status)
    return f'<span class="status-tag {css_class}">{safe_status}</span>'


def _render_execution_status_tag(status: str) -> str:
    """根据执行状态渲染带颜色样式的状态标签。"""
    css_map = {
        "success": "execution-status-success",
        "failed": "execution-status-failed",
        "running": "execution-status-running",
    }
    css_class = css_map.get(status.lower(), "")
    safe_status = html.escape(status)
    return f'<span class="status-tag {css_class}">{safe_status}</span>'
