# Adwatch

面向 TikTok Shop 与 Shopee 跨境电商团队的本地经营与广告决策系统。
Adwatch 把平台广告、订单、SKU 成本、费用、资金和合伙人分润统一到
SQLite，在一套数据口径上完成利润分析、异常告警、广告调优建议、对账和
安全执行。

当前项目运行在 macOS，通过紫鸟开放平台 CLI 读取真实店铺页面。真实广告
写入默认关闭。

## 项目简介

跨境店铺的数据通常散落在广告后台、订单报表、成本表、记账表和聊天记录里。
平台 ROAS 看起来不错，并不一定代表订单真正赚钱；充值、广告消耗、采购付款
和销售成本也容易被重复计算。

Adwatch 以“每条订单可追溯、每项成本有口径、每个广告动作可审计”为原则，
形成下面的闭环：

```text
TikTok / Shopee / 人工经营数据
                ↓
        SQLite 统一数据底座
                ↓
订单成本 · 利润 · ROAS · 库存/履约 · 分润
                ↓
      异常告警与广告调优建议
                ↓
    人工审批 · Shadow · Live 安全执行
```

## 解决的问题

- 把 TikTok 与 Shopee 广告指标放进同一数据模型。
- 把平台订单关联到 Seller SKU、历史成本和履约方式。
- 区分平台 ROAS、净销售 ROAS 与利润 ROAS。
- 让费用、采购、广告充值、实际消耗、提现和分润各归其位。
- 支持货盘代发与自有备货 SKU 在同一店铺共存。
- 用连续对账、审批、熔断和白名单约束自动广告操作。
- 保留原始数据、成本快照、审批、执行和回滚证据，便于复查。

## 核心功能

### 双平台真实采集

- 通过紫鸟 CLI 控制本地店铺浏览器。
- 采集 TikTok Shop GMV Max 与 Shopee Ads 指标。
- 数据按平台、店铺、账号、Campaign、SKU 和日期幂等写入。
- 生成采集质量报告，模拟数据和真实数据不会混淆。

### 订单与 SKU 成本

- 导入 Shopee/TikTok 订单明细和中文表头成本表。
- 一个订单可以包含多行 SKU。
- SKU 成本采用带生效日期的历史版本。
- 订单处理时冻结成本和履约快照，后续改价不篡改历史利润。
- 取消订单不计销售成本，确认退货按规则冲销。

### SKU 级履约

- `supplier_fulfilled`：货盘代发，订单计入成本和利润，不要求库存。
- `stocked`：自有备货，采购或期初库存入库，销售和退货生成库存流水。
- 履约方式配置在 SKU，而不是店铺；同一店铺可同时使用两种模式。

### 经营记账与合伙人分润

- 管理费用支出、刷单成本、采购付款、广告充值和平台收入。
- 平台原始结算记录不可人工覆盖，只能新增调整或冲销。
- 采购付款进入现金与库存，不直接重复扣利润。
- 广告充值进入广告预付余额，实际广告消耗才进入利润。
- 合伙人出资按 50%/50% 对账，净利润按当前协议 60%/40% 分配。
- 分润支持按期间生成草稿并分次登记实际支付。

### 利润与广告分析

- 平台 ROAS：平台归因 GMV ÷ 广告消耗。
- 净销售 ROAS：扣除取消、退款和刷单后的销售额 ÷ 广告消耗。
- 利润 ROAS：广告前订单贡献毛利 ÷ 广告消耗。
- 计算商品成本、平台佣金、运费、优惠、固定费用、退款和净利润。
- 为 GMV、退款、费率、商品成本、广告花费、运费、优惠、固定成本和汇率逐项
  保存来源、来源日期和可信状态；退款金额未知时利润保持待补，不按 0 计算。
- 支持按日期范围重算并生成 added/changed/unchanged/removed 差异报告。
- 识别低 ROAS、高 CPA、库存/供货风险和全局异常。
- 输出增预算、降预算、调 ROAS、暂停和恢复建议。
- 支持策略回放，验证历史日期使用的规则与当前实现一致。

### 对账、报告与看板

- 日报、周报、月报和采集质量报告。
- 金额与 ROAS 使用数值容差对账，订单数精确对账。
- 连续三个自然日核心准确率达到 99% 才允许通过 Live 门禁。
- 本地 Web 看板覆盖经营、广告、SKU、库存、资金、分润与审批执行。
- SQLite 在线备份、完整性校验和恢复演练。
- macOS `launchd` 每日定时任务。

## 广告调优与安全执行

系统把“分析建议”和“真实修改”严格分开：

1. 分析引擎生成建议和证据。
2. 需要写操作的建议创建人工审批。
3. Shadow 读取真实页面并暂存预期值，不点击提交。
4. Live 只有在所有门禁通过后才允许提交。
5. 执行前后截图、页面状态、错误和回滚结果写入审计记录。

飞书点击“批准”只会更新审批状态，不会自动修改广告。真实广告修改还需要
独立执行命令，并同时满足：

- 审批未过期，且建议在审批后没有变化；
- 当前页面状态与审批时的预期状态一致；
- 写操作熔断器关闭；
- 对应平台与动作已经完成真实页面选择器激活；
- `ADWATCH_LIVE_WRITES=true`；
- `platform:store_id:campaign_id` 精确命中允许清单；
- 动作不在永久禁止列表中。

删除 Campaign、修改账号/店铺/安全设置和大规模新建 Campaign 永久禁止。
飞书公网 HTTPS 回调代码已经具备，但现场接入暂缓；当前不会影响数据采集、
利润分析和 Shadow 验证。

## 经营与利润口径

| 业务事件 | 影响现金 | 影响库存 | 影响当期利润 |
| --- | ---: | ---: | ---: |
| 合伙人实缴 | 是 | 否 | 否 |
| 采购付款 | 是 | 是 | 否 |
| 销售出库成本 | 否 | 是 | 是 |
| 广告充值 | 是 | 否 | 否 |
| 广告实际消耗 | 否 | 否 | 是 |
| 平台结算收入 | 是 | 否 | 是 |
| 费用与刷单成本 | 是 | 否 | 是 |
| 合伙人分润支付 | 是 | 否 | 否 |

这套口径避免把采购付款与销售成本、广告充值与广告消耗重复扣减。

## 系统架构

```text
紫鸟 CLI / 平台报表 / XLSX·CSV
              │
              ▼
       Collectors & Importers
              │
              ▼
         SQLite Storage
       ┌──────┼────────┐
       ▼      ▼        ▼
  Analytics  Ledger  Reconciliation
       │      │        │
       └──────┼────────┘
              ▼
 Reports · Dashboard · Recommendations
              │
              ▼
 Approval · Shadow · Live · Audit
```

主要技术：

- Python 3.11+
- SQLite（WAL、版本迁移、在线备份）
- 紫鸟开放平台 CLI 与 ZClaw Bridge
- XLSX/CSV 经营数据导入
- 本地 HTTP 看板
- pytest 与 Ruff

所有业务数据默认保存在本机 `var/`，不要求部署数据库服务器。

## 快速开始

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env

.venv/bin/adwatch init
.venv/bin/adwatch doctor
```

在 `.env` 填写紫鸟店铺 ID 后，可执行真实采集：

TikTok 采集默认禁用，当前只采 Shopee。以后完成 TikTok 现场验收后，可显式设置
`ADWATCH_TIKTOK_COLLECTION_ENABLED=true`；该开关同时作用于手工采集和每日
定时任务，不会删除店铺配置或历史数据。

```bash
.venv/bin/adwatch collect --mode ziniao --date 2026-07-27
.venv/bin/adwatch orders sync --platform shopee \
  --from 2026-07-27 --to 2026-07-27
.venv/bin/adwatch income scan
.venv/bin/adwatch income list
.venv/bin/adwatch business sync-orders
.venv/bin/adwatch analyze --date 2026-07-27
.venv/bin/adwatch analyze --from 2026-07-01 --to 2026-07-27
.venv/bin/adwatch report daily --date 2026-07-27
```

`orders sync` 从紫鸟内已登录的 Shopee 会话只读同步商品映射和平台订单；
同时保存退款状态、物流状态和平台库存的按日事实。每天 09:00 默认回看报告日
及之前 3 天（`ADWATCH_SHOPEE_HISTORY_LOOKBACK_DAYS=3`），使晚到的退款状态
可以被重新观测；订单卡没有可靠退款金额时保存为缺失，不会按 0 处理。
`business sync-orders` 随后处理已经落库的订单成本快照与履约，不负责平台采集。
成本和履约解析会通过 `store_aliases` 兼容紫鸟原始店铺 ID 与经营页规范店铺名，
订单快照仍保存订单自身的店铺口径。
`/inventory` 会按订单发生日列出缺少有效成本版本的 SKU，可在清单中直接补录
成本，也可在同页维护履约方式和采购入库。

`analyze --from/--to` 只重算范围内存在广告指标的日期，默认把差异报告写到
`var/reports/profit-recalculation-<from>-<to>.json`。重复重算应显示利润与输入
lineage 为 unchanged；报告只保存汇总差异，不包含订单号或完整本机路径。

启动本地看板：

```bash
.venv/bin/adwatch dashboard \
  --host 127.0.0.1 --port 8765 --date 2026-07-27
```

查看上线门禁：

```bash
.venv/bin/adwatch launch-checklist --format markdown
```

## macOS 每日自动运行

当前仓库是 Adwatch 唯一生产代码源，生产数据保存在当前仓库的 `var/`。
受管 LaunchAgent 在北京时间每天 09:00 运行；登录时会自动加载，并在
09:00 后发现前一日日报未成功时补跑一次。

先确认 Mac 系统时区为 `Asia/Shanghai`，再安装并检查任务：

```bash
.venv/bin/adwatch schedule install
.venv/bin/adwatch schedule status
```

每日顺序固定为：获取执行日 THB/CNY 汇率（无当天值时依次使用昨日和 7 天
内最近有效交易日）→ 扫描并导入 Income 周结算 PDF → 检查紫鸟 Bridge → 回看并同步 Shopee 商品、
订单、退款状态、物流和平台库存 → 采集已启用平台广告 → 分析 →
生成本地报告 → 发送飞书。Bridge 不可用时不会进入广告采集。

人工补跑并禁止所有飞书外发：

```bash
.venv/bin/adwatch run daily \
  --mode ziniao --date 2026-07-29 --no-notify
```

修复漂移或卸载任务：

```bash
.venv/bin/adwatch schedule repair
.venv/bin/adwatch schedule uninstall
```

独立运行状态和故障证据位于：

- `var/operations/daily-schedule-state.json`
- `var/operations/scheduled-daily.lock`
- `var/logs/adwatch-faults.jsonl`
- `var/logs/launchd.out.log`
- `var/logs/launchd.err.log`

详细安装、补跑、重启和次日 09:00 验收步骤见
[macOS 调度运维手册](docs/operations/adwatch-macos-schedule.md)。

紫鸟 CLI 安装和授权请参考[紫鸟官方页面](https://open.ziniao.com/ziniaoCli)。
完整命令可通过 `.venv/bin/adwatch --help` 和各子命令 `--help` 查看。
剩余能力的实施顺序和验收门槛见
[AdWatch 剩余功能闭环开发计划](docs/plans/2026-08-04-adwatch-remaining-gap-closure.md)。

## 当前状态

| 能力 | 状态 |
| --- | --- |
| SQLite、利润输入 lineage、范围重算、日报和基础看板 | 已完成；生产利润因逐订单退款金额和部分订单行成本覆盖不足而诚实待补 |
| 经营账、余额对账、库存与分润专属页面 | 已完成代码闭环；生产经营表仍为空，等待真实业务启用 |
| 紫鸟 Boss 授权、API、Bridge 与真实店铺 | 已连通 |
| Shopee 真实广告采集 | 已完成 |
| Shopee 订单、SKU 成本和货盘代发履约 | 已完成 |
| Shopee 退款状态 T+3、物流和平台库存事实 | 已接入；逐订单退款金额等待独立明细来源 |
| Shopee Income 结算、平台费率与广告充值 | 已接入每日扫描并通过真实 PDF 幂等验收 |
| 今日经营来源可信度与广告调优门禁 | 已接入；按店解释订单、成本、退款、库存、汇率和费率缺口 |
| Shopee 首日真实对账 | 100%，0 项差异 |
| TikTok 广告采集 | 当前按配置禁用，保留历史数据与代码 |
| Shadow、回滚、页面选择器 | 现场激活中 |
| 连续三日 99% 对账和 Live 精确白名单 | 现场激活中 |
| 飞书公网审批回调 | 代码已具备，现场暂缓 |
| TikTok/Shopee 官方 API OAuth | 可选，当前使用紫鸟 CLI |

## 财务、库存与分润闭环

迁移 25 增加受管凭证、人工财务调整、现金/平台/银行余额快照和差额解决链，
并为费用、出资、提款、广告资金、刷单成本、采购、库存移动、分润期间和付款补齐
责任人、原因、审计和单次冲销约束。确认后的原事实不会物理删除；修复通过反向
现金、反向库存 movement 或带 `reversal_of` 的付款/调整记录完成。

专属页面职责如下：

- `/ad-funds`：费用草稿/确认/冲销、收入/现金/余额调整、广告资金、余额快照、
  三方差额和凭证。
- `/inventory`：待补成本、履约、采购、人工盘点、采购/人工流水冲销和凭证。
- `/profit-sharing`：协议、期间、确认、付款、付款/期间冲销和凭证。

凭证仅接受 PDF、JPEG、PNG，最大 5 MiB。系统校验内容魔数后使用 UUID 文件名
原子写入 `ADWATCH_EVIDENCE_ROOT`（默认 `ADWATCH_DATA_DIR/evidence`）；数据库不
保存用户路径。下载固定为 attachment，并在读取前复验大小和 SHA-256。

项目不会用模拟数据、空 Campaign 或手工状态冒充真实现场验收。

## 开发验证

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python -m ruff check src tests
```

更多设计、数据口径和实施记录位于 `docs/`。
