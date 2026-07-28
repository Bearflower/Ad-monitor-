import html

NAVIGATION = (
    ("/", "今日经营"),
    ("/optimization", "广告调优"),
    ("/ad-funds", "收入与广告资金"),
    ("/inventory", "SKU与库存"),
    ("/operations", "记账对账"),
    ("/profit-sharing", "合伙人分润"),
    ("/approvals", "审批执行"),
)


def render_navigation(active_path: str) -> str:
    links = []
    for path, label in NAVIGATION:
        current = ' aria-current="page"' if path == active_path else ""
        links.append(
            f'<a href="{path}"{current}>{html.escape(label)}</a>'
        )
    return '<nav aria-label="业务导航">' + "".join(links) + "</nav>"


def render_operations_forms(csrf_token: str) -> str:
    token = html.escape(csrf_token, quote=True)
    return f"""
    <section class="panel">
      <h2>费用／前期投入</h2>
      <form method="post" action="/expenses">
        <input type="hidden" name="csrf_token" value="{token}">
        <label>日期 <input type="date" name="occurred_on" required></label>
        <label>费用类别 <input name="category" required></label>
        <label>金额 <input name="amount" inputmode="decimal" required></label>
        <label>币种 <input name="currency" value="CNY" required></label>
        <label>汇率 <input name="rate_to_cny" value="1" required></label>
        <label>付款人 <input name="payer" required></label>
        <label>资金性质 <select name="fund_nature">
          <option value="operating_expense">经营费用</option>
          <option value="capital_contribution">合伙人实缴</option>
          <option value="partner_advance">合伙人代垫</option>
          <option value="adjustment">资金调整</option>
        </select></label>
        <label><input type="checkbox" name="affects_profit" value="1">
          计入利润</label>
        <label><input type="checkbox" name="affects_capital" value="1">
          计入出资对账</label>
        <label>备注 <textarea name="note"></textarea></label>
        <button type="submit">保存草稿</button>
      </form>
    </section>
    <section class="panel"><h2>合伙人出资与提款</h2>
      <form method="post" action="/capital">
        <input type="hidden" name="csrf_token" value="{token}">
        <input type="date" name="occurred_on" required>
        <input name="partner" placeholder="合伙人" required>
        <select name="entry_type"><option value="paid_in">实缴</option>
          <option value="advance">代垫</option></select>
        <input name="amount" placeholder="人民币金额" required>
        <button>登记出资</button>
      </form>
      <form method="post" action="/withdrawals">
        <input type="hidden" name="csrf_token" value="{token}">
        <input type="date" name="occurred_on" required>
        <input name="partner" placeholder="提款人" required>
        <input name="amount" placeholder="人民币金额" required>
        <input name="purpose" placeholder="用途" required>
        <button>登记提款</button>
      </form>
    </section>
    <section class="panel"><h2>广告充值与刷单成本</h2>
      <form method="post" action="/ad-funding">
        <input type="hidden" name="csrf_token" value="{token}">
        <input type="date" name="occurred_on" required>
        <input name="platform" placeholder="平台" required>
        <input name="store" placeholder="店铺" required>
        <select name="entry_type"><option value="recharge">充值</option>
          <option value="gift">赠送</option>
          <option value="refund">退款</option></select>
        <input name="amount" placeholder="人民币金额" required>
        <input name="source" value="manual" required>
        <button>登记广告资金</button>
      </form>
      <form method="post" action="/review-costs">
        <input type="hidden" name="csrf_token" value="{token}">
        <input type="date" name="occurred_on" required>
        <input name="platform" placeholder="平台" required>
        <input name="store" placeholder="店铺" required>
        <input name="order_id" placeholder="刷单订单号" required>
        <input name="seller_sku" placeholder="Seller SKU">
        <input name="goods_cost" placeholder="货款" required>
        <input name="service_fee" placeholder="服务费" required>
        <button>登记并排除真实指标</button>
      </form>
    </section>
    <section class="panel"><h2>SKU 成本与采购入库</h2>
      <form method="post" action="/fulfillment">
        <input type="hidden" name="csrf_token" value="{token}">
        <input name="platform" placeholder="平台" required>
        <input name="store" placeholder="店铺" required>
        <input name="seller_sku" placeholder="Seller SKU" required>
        <input type="date" name="effective_date" required>
        <select name="mode">
          <option value="supplier_fulfilled">货盘代发</option>
          <option value="stocked">自有备货</option>
        </select>
        <select name="supply_status">
          <option value="available">可供货</option>
          <option value="paused">暂停供货</option>
        </select>
        <input name="note" placeholder="履约备注">
        <button>新增履约方式版本</button>
      </form>
      <form method="post" action="/sku-costs">
        <input type="hidden" name="csrf_token" value="{token}">
        <input name="platform" placeholder="平台" required>
        <input name="store" placeholder="店铺" required>
        <input name="seller_sku" placeholder="Seller SKU" required>
        <input type="date" name="effective_date" required>
        <input name="unit_cost_cny" placeholder="人民币单位成本" required>
        <input name="note" placeholder="备注">
        <button>新增成本版本</button>
      </form>
      <form method="post" action="/purchases">
        <input type="hidden" name="csrf_token" value="{token}">
        <input name="receipt_id" placeholder="采购单号" required>
        <input name="supplier" placeholder="供应商" required>
        <input type="date" name="received_on" required>
        <input name="seller_sku" placeholder="Seller SKU" required>
        <input name="quantity" placeholder="入库数量" required>
        <input name="unit_cost_cny" placeholder="人民币单位成本" required>
        <button>采购入库</button>
      </form>
    </section>
    <section class="panel"><h2>分润协议</h2>
      <form method="post" action="/profit-agreements">
        <input type="hidden" name="csrf_token" value="{token}">
        <input type="date" name="effective_from" required>
        <label>洁云比例 <input name="jieyun_share" value="0.60"></label>
        <label>苏姐比例 <input name="sujie_share" value="0.40"></label>
        <button>新增协议版本</button>
      </form>
      <form method="post" action="/profit-periods">
        <input type="hidden" name="csrf_token" value="{token}">
        <label>结算开始 <input type="date" name="starts_on" required></label>
        <label>结算结束 <input type="date" name="ends_on" required></label>
        <button>按经营账生成分润草稿</button>
      </form>
      <form method="post" action="/profit-payments">
        <input type="hidden" name="csrf_token" value="{token}">
        <input name="period_id" placeholder="分润期间 ID" required>
        <input name="partner" placeholder="收款人" required>
        <input name="amount_cny" placeholder="实付人民币" required>
        <input type="date" name="paid_on" required>
        <select name="status"><option value="planned">计划</option>
          <option value="paid">已支付</option></select>
        <input name="note" placeholder="备注">
        <button>记录分润支付</button>
      </form>
      <form method="post" action="/profit-periods/confirm">
        <input type="hidden" name="csrf_token" value="{token}">
        <input name="period_id" placeholder="分润期间 ID" required>
        <button>确认分润期间</button>
      </form>
    </section>
    """


def render_operations_page(csrf_token: str) -> str:
    return f"""<!doctype html>
    <html lang="zh-CN"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Adwatch 记账对账</title></head><body>
    {render_navigation("/operations")}
    <main><h1>记账对账</h1>{render_operations_forms(csrf_token)}</main>
    </body></html>"""


def render_module_page(path: str, title: str, summary: str) -> str:
    return f"""<!doctype html><html lang="zh-CN"><head>
    <meta charset="utf-8"><meta name="viewport"
    content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
    </head><body>{render_navigation(path)}<main><h1>{html.escape(title)}</h1>
    <p>{html.escape(summary)}</p></main></body></html>"""


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
    items = "".join(f"<li>{html.escape(item)}</li>" for item in evidence)
    return f"""
    <section class="panel">
      <h2>广告调优</h2>
      <dl>
        <div><dt>平台 ROAS</dt><dd>{html.escape(platform_roas)}</dd></div>
        <div><dt>净销售 ROAS</dt><dd>{html.escape(net_sales_roas)}</dd></div>
        <div><dt>利润 ROAS</dt><dd>{html.escape(profit_roas)}</dd></div>
        <div><dt>数据可信度</dt><dd>{html.escape(confidence)}</dd></div>
      </dl>
      <h3>建议与证据</h3>
      <p>{html.escape(action)}：{html.escape(before)} → {html.escape(after)}</p>
      <ul>{items}</ul>
      <p>执行状态：{html.escape(execution_status)}</p>
    </section>
    """
