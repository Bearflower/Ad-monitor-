# Income 结算报表自动导入设计

日期：2026-07-28  
状态：用户已确认业务口径

## 目标

每天运行 Adwatch 前，自动扫描紫鸟店铺下载目录中的新 Income PDF，提取平台实际销售费用，按平台、店铺和结算周期保存费率版本。利润分析优先使用数据日期所在周期的费率；没有精确覆盖时，沿用该日期之前最近一期费率。

## 已确认业务口径

1. 每份周报计算该周的实测综合销售费率。
2. 报表覆盖日期使用该周费率。
3. 报表覆盖期结束后，在下一份报表出现前继续沿用最近一期费率。
4. 不回写或改写已有历史周期的来源记录。
5. Shopee 与 TikTok 分平台、分店铺维护，禁止共用全局费率。
6. 综合销售费率只包括销售相关费用：
   - Commission Fee
   - Service Fee
   - Transaction Fee
   - AMS Commission Fee
7. 以下项目单独保存，不并入综合销售费率：
   - Ads Credit Top-Up
   - Voucher Sponsored by Seller
   - Shipping Fee Paid by Buyer
   - Shipping Fee Charged by Logistic Provider
   - Shipping Rebate From Platform
   - Order Adjustment

## 数据模型

新增 `platform_fee_statements`：

| 字段 | 说明 |
| --- | --- |
| `platform` | `shopee` 或 `tiktok` |
| `store` | 系统标准店铺标识 |
| `period_start` | 报表覆盖开始日期 |
| `period_end` | 报表覆盖结束日期 |
| `currency` | 原始报表币种 |
| `product_price` | 商品价合计 |
| `commission_fee` | 平台佣金 |
| `service_fee` | 服务费 |
| `transaction_fee` | 交易手续费 |
| `affiliate_fee` | AMS/Affiliate 等销售佣金 |
| `sales_fee_total` | 上述销售费用合计 |
| `effective_rate` | `sales_fee_total ÷ product_price` |
| `seller_voucher` | 卖家承担优惠 |
| `shipping_paid_by_buyer` | 买家支付运费 |
| `shipping_provider_charge` | 物流商扣费 |
| `shipping_rebate` | 平台物流补贴 |
| `ads_credit_topup` | 广告充值或托管扣款 |
| `adjustment_amount` | 调账金额 |
| `total_payout` | 最终结算金额 |
| `source_path` | 来源 PDF 路径 |
| `source_sha256` | 文件内容哈希 |
| `imported_at` | 导入时间 |

唯一约束：

```text
platform + store + period_start + period_end
```

`source_sha256` 另设唯一索引，避免同一文件被重复导入。

旧表 `product_costs.commission_rate` 保留用于兼容历史手工输入，但分析时按以下优先级取值：

1. 数据日期落在 `platform_fee_statements` 覆盖期内；
2. 数据日期之前最近一期平台/店铺费率；
3. 旧版 SKU 成本费率；
4. 无可用费率时标记 `pending_data`，禁止猜测。

## 文件发现与平台适配

默认扫描紫鸟数据目录：

```text
/Users/yl/Library/Application Support/ziniaobrowserdatas/
```

只读取名称匹配 `weekly_report_*.pdf` 的新文件。扫描器不移动、不重命名、不删除原文件。

解析采用适配器结构：

```text
IncomeStatementImporter
├── ShopeeIncomePdfParser
└── TikTokIncomeParser（获得真实样本后实现）
```

Shopee 解析器负责当前 Income Statement 格式。TikTok 没有真实结算报表样本时，返回 `pending_external`，不得套用 Shopee 字段。

平台和店铺识别：

- 优先读取 PDF 内的 Username/店铺标识；
- 再通过 `store_aliases` 映射到系统标准店铺；
- 无法唯一识别时隔离文件并生成告警，不导入费率。

## 每日流程

每天北京时间 10:00：

```text
扫描 Income 文件
→ 校验文件哈希
→ 解析并保存新费率版本
→ 采集前一天广告数据和订单
→ 同步 SKU 成本及履约状态
→ 选择对应平台/店铺费率
→ 计算利润和广告建议
→ 生成日报
→ 发送飞书
```

Income 文件缺失或解析失败不阻塞广告数据采集；日报标注使用的费率版本或 `pending_data`。

## 计算规则

```text
sales_fee_total =
    commission_fee
  + service_fee
  + transaction_fee
  + affiliate_fee

effective_rate = sales_fee_total / product_price
```

所有费用在数据库中保存为正数成本。PDF 中的负号只表示结算扣减，解析时转换为成本绝对值。

当 `product_price <= 0` 时拒绝计算费率并隔离记录。

示例：

```text
Product Price          4,473
Commission Fee           500
Service Fee              379
Transaction Fee          150
AMS Commission Fee        32

sales_fee_total = 1,061
effective_rate = 1,061 / 4,473 = 23.7201%
```

Ads Credit Top-Up `13` 单独保存，不进入 `effective_rate`。

## 命令和运行状态

新增命令：

```text
adwatch income import --file <pdf> --platform shopee
adwatch income scan
adwatch income list
```

- `import`：人工指定单份文件，用于复核和补录。
- `scan`：扫描默认目录并导入全部未处理文件。
- `list`：展示平台、店铺、周期、费率和来源文件。

每日任务在采集前调用与 `income scan` 相同的服务，不通过 shell 启动第二个进程。

## 审计与错误处理

- 每份成功导入的 PDF 保存来源路径和 SHA-256。
- 同一周期出现修订版时，更新周期数据并保留审计事件。
- 字段缺失、金额合计不一致或店铺无法识别时写入隔离记录。
- 解析失败发送飞书提示，但不阻断 Shopee/TikTok 广告采集。
- 日报展示使用的费率、覆盖周期和来源文件名。
- 不在日志或飞书中输出姓名、银行账号和地址等 PDF 隐私字段。

## 测试与验收

自动化测试覆盖：

1. 解析当前两页 Shopee Income PDF。
2. 正确计算 `23.7201%`。
3. 排除 Ads Credit Top-Up、优惠券和物流项目。
4. 同一 SHA-256 重复扫描不重复导入。
5. 同周期修订版产生审计记录。
6. 周期内日期使用精确周费率。
7. 周期结束后沿用最近一期费率。
8. 不允许 Shopee 费率串到 TikTok 或其他店铺。
9. 无法识别店铺时隔离且不阻塞每日任务。
10. 每日飞书报告显示实际采用的费率版本。

现场验收：

- 导入 `weekly_report_20260720 (1).pdf`；
- 数据库记录 Shopee/no4kud44da、2026-07-20 至 2026-07-26；
- 综合销售费率为 `0.237201`；
- 2026-07-20 至 2026-07-26 使用精确覆盖；
- 2026-07-27 至下一份周报导入前继续沿用 `0.237201`；
- 日报不再出现 `Missing business inputs: commission_rate`。

## 不在本次范围

- 自动输入 Shopee 登录密码。
- 自动绕过收入页面二次验证。
- 修改或删除原始 Income PDF。
- 在没有真实 TikTok 结算报表样本时猜测 TikTok 字段或费率。
- 开启广告 Live 白名单。
