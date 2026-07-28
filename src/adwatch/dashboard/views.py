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
    """
