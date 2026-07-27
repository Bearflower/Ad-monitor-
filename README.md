# Adwatch

TikTok 与 Shopee 广告盯盘自动化系统。当前版本提供 SQLite
数据底座、双平台采集、利润与异常分析、策略建议、审批回调、
Shadow/Live 安全执行框架、日报/周报/月报、备份和本地只读看板。
真实广告写入默认关闭。

## 环境要求

- macOS
- Python 3.11 或更高版本

## 安装

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

程序读取系统环境变量和项目根目录的 `.env`。

## 初始化

```bash
export ADWATCH_DATA_DIR="$PWD/var"
python -m adwatch init
python -m adwatch doctor
```

## 运行模拟采集

```bash
python -m adwatch collect --mode mock --date 2026-07-22
```

命令会采集 TikTok 和 Shopee 模拟数据，写入 `var/adwatch.sqlite3`，并生成 `var/reports/quality-2026-07-22.json`。报告包含 `"simulated": true`，用于防止模拟数据被误认为真实业务数据。

同一日期可以安全重跑。系统依据平台、店铺、账户、Campaign、SKU 和日期更新同一条逻辑记录，不会重复累加。

## 紫鸟模式

配置下列店铺 ID 后才能显式使用 `--mode ziniao`：

- `ZINIAO_TIKTOK_STORE_ID`
- `ZINIAO_SHOPEE_STORE_ID`

缺少配置时命令返回非零状态并说明缺失项。系统不会静默切换到模拟数据。

紫鸟 CLI 应按[紫鸟官方安装页](https://open.ziniao.com/ziniaoCli)完成安装和授权。系统已实现官方 WebDriver 本地 HTTP 控制动作与健康检查；TikTok/Shopee 页面报表选择器及写操作需在真实店铺配置完成后现场核验，未核验前保持禁用。

## 一键每日流程

```bash
python -m adwatch run daily --mode mock --date 2026-07-22
```

该命令依次完成采集、质量校验、经营数据模拟补齐、利润分析、策略建议、日报和飞书投递；未配置飞书时自动保存本地 Markdown。

## 补齐真实经营参数

真实模式不会猜测成本、库存、汇率或目标值。先根据已经采集的广告记录导出 CSV：

```bash
.venv/bin/adwatch business export-template \
  --from 2026-07-21 \
  --to 2026-07-23 \
  --output var/business-inputs-2026-07-21_2026-07-23.csv
```

填写所有空白列后导入，并重新分析：

```bash
.venv/bin/adwatch business import \
  --file var/business-inputs-2026-07-21_2026-07-23.csv
.venv/bin/adwatch analyze --date 2026-07-23
```

CSV 会整批校验；任意一行缺值或格式错误时，不会写入任何一行。目前 Shopee 采集结果的 `sku_id` 是 `__ALL__`，代表 Campaign 当日汇总，因此 `product_cost`、运费、优惠、固定成本和退款应填写该 Campaign 当日总额；`commission_rate` 填小数，例如 8% 填 `0.08`。`rate_to_cny` 表示 1 单位广告币种折合多少人民币。

若一天只有一条汇总广告记录，可使用最小三列 CSV：

```csv
data_date,total_product_cost,refund_amount
2026-07-23,120,0
```

```bash
.venv/bin/adwatch business import-minimal --file minimal-costs.csv
```

系统自动使用已采集的订单、GMV 和广告花费。若同一天有多条广告记录，
最小模式会拒绝导入以防成本被错误分摊，应改用完整模板。

也可以直接导入中文表头的订单 SKU 成本明细 XLSX/CSV：

```bash
.venv/bin/adwatch business import-orders \
  --file /Users/yl/Desktop/订单SKU成本明细模板-shopee2.xlsx
.venv/bin/adwatch business map-store \
  --platform shopee --source no4kud44da --target 虾皮泰国
.venv/bin/adwatch business order-summary \
  --from 2026-07-08 --to 2026-07-17
```

表头固定为 `日期,平台,店铺,订单号,SKU,数量,单件成本_人民币`。`数量`
表示购买的可销售规格件数：一个 `5 bags` 规格填写数量 `1`、单件成本
`17`；只有购买两个完整的 `5 bags` 规格时数量才填写 `2`。同一订单的
不同 SKU 使用相同订单号分多行。重复导入采用幂等更新，不重复累计成本。
订单成本已经是人民币，利润分析不会再次按泰铢汇率换算。

## 报告、备份与上线检查

```bash
.venv/bin/adwatch report weekly --end 2026-07-26
.venv/bin/adwatch report monthly --month 2026-07
.venv/bin/adwatch backup create --output var/backups/manual.sqlite3
.venv/bin/adwatch backup verify --path var/backups/manual.sqlite3
.venv/bin/adwatch readiness
.venv/bin/adwatch launch-checklist --format markdown
```

`launch-checklist` 会把需要真实账号、真实数据或公网配置才能完成的事项
集中列出，避免把外部依赖误报成代码缺陷。

## 审批与安全执行

飞书回调需要 `FEISHU_CALLBACK_SECRET` 和公网 HTTPS 地址：

```bash
.venv/bin/adwatch approval serve --host 127.0.0.1 --port 8787
```

审批通过后先运行 Shadow。它会读取真实页面并记录预期改动，但不会点击提交：

```bash
.venv/bin/adwatch execute shadow \
  --approval-id APPROVAL_ID \
  --idempotency-key UNIQUE_RUN_ID \
  --expected-before '{"budget":"100"}'
```

Live 必须同时满足审批有效、熔断关闭、全局开关开启和
`platform:store_id:campaign_id` 精确白名单命中。默认配置
`ADWATCH_LIVE_WRITES=false`，因此不会修改真实广告。

此外，每个 TikTok/Shopee 动作都必须完成现场选择器激活。激活配置需要
真实页面验证产生的前后证据截图：

```bash
.venv/bin/adwatch activation register \
  --platform shopee \
  --action reduce_budget \
  --version 2026-07-27 \
  --store-id STORE_ID \
  --selectors-file selectors.json \
  --activated-by BOSS \
  --evidence-before var/screenshots/before.png \
  --evidence-after var/screenshots/after.png

.venv/bin/adwatch activation list
```

未激活的动作即使误开 Live 总开关也会在首次页面读取之前被拒绝。
适配器只调用固定的紫鸟 `page query/input/click/screenshot` 动作，
不接受调用方传入任意 JavaScript。

## 本地看板

```bash
python -m adwatch dashboard --host 127.0.0.1 --port 8765 --date 2026-07-22
```

浏览器打开 `http://127.0.0.1:8765`。看板只读，远程监听必须显式添加 `--allow-remote`。

## 调度配置

```bash
python -m adwatch schedule --print-launchd
```

命令输出每日 08:00 执行所需的 macOS `launchd` 配置，不会自动安装系统服务。

## 开发验证

```bash
python -m pytest -q
python -m ruff check .
```
