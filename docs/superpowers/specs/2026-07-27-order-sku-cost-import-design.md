# 订单 SKU 成本明细导入设计

日期：2026-07-27  
状态：已确认

## 目标

让经营人员只维护一张订单商品成本明细表，系统即可：

- 直接读取中文表头的 `.xlsx` 或 `.csv`；
- 支持同一订单包含多个 SKU；
- 保存人民币商品成本明细，重复导入不重复累计；
- 按平台、店铺和日期汇总商品成本；
- 在不会重复分摊的前提下，将成本提供给广告利润分析；
- 保持广告写操作关闭，不影响 Shadow/Live 安全门。

首个验收文件为：

`/Users/yl/Desktop/订单SKU成本明细模板-shopee2.xlsx`

## 输入契约

固定中文表头：

| 列名 | 含义 | 规则 |
|---|---|---|
| 日期 | 平台订单日期 | 接受 `YYYYMMDD`、`YYYY-MM-DD` 或 Excel 日期 |
| 平台 | 订单平台 | 规范化为小写；首期接受 `shopee`、`tiktok` |
| 店铺 | 平台店铺名称或稳定 ID | 去除首尾空格，不允许为空 |
| 订单号 | 平台订单号 | 按文本保存，不做数值转换 |
| SKU | 可销售规格 | 例如 `1 bag`、`3 bags`、`5 bags` |
| 数量 | 购买的该规格件数 | 必须为正整数 |
| 单件成本_人民币 | 一个该规格的商品成本 | 必须为非负十进制数，最多 4 位小数 |

每行成本：

`行商品成本人民币 = 数量 × 单件成本_人民币`

示例：顾客购买两个 `3 bags` 规格时，数量为 `2`，单件成本为
`11`，行成本为 `22`。规格本身包含的袋数不能再次写入数量。

## 数据模型

新增 `order_cost_lines`：

| 字段 | 类型/约束 |
|---|---|
| platform | `TEXT NOT NULL` |
| store | `TEXT NOT NULL` |
| order_id | `TEXT NOT NULL` |
| sku_id | `TEXT NOT NULL` |
| order_date | ISO 日期，`TEXT NOT NULL` |
| quantity | `INTEGER NOT NULL CHECK(quantity > 0)` |
| unit_cost_cny | 十进制字符串，`TEXT NOT NULL` |
| line_cost_cny | 十进制字符串，`TEXT NOT NULL` |
| source_file | `TEXT NOT NULL`，仅保存文件名，不保存桌面绝对路径 |
| updated_at | UTC 时间 |

主键为：

`(platform, store, order_id, sku_id)`

同一订单的不同 SKU 可以分多行；同一订单的相同 SKU 应在源文件中合并。
再次导入相同主键时，更新日期、数量、成本和来源，不新增重复记录。
源文件删除一行不会自动删除数据库历史记录，防止一次不完整文件造成数据丢失。

新增 `store_aliases`：

| 字段 | 类型/约束 |
|---|---|
| platform | `TEXT NOT NULL` |
| source_store | 订单文件中的店铺名，`TEXT NOT NULL` |
| canonical_store | `daily_ad_metrics` 中的店铺名，`TEXT NOT NULL` |

主键为 `(platform, source_store)`。订单文件继续使用平台真实店名，不要求经营人员
改成紫鸟内部显示名。首个现场映射为
`shopee/no4kud44da -> 虾皮泰国`。

## 导入流程

新增命令：

```bash
.venv/bin/adwatch business import-orders \
  --file /path/to/orders.xlsx
```

流程：

1. 根据扩展名选择 XLSX 或 CSV 读取器。
2. 读取第一个非空工作表；XLSX 只读取计算后的单元格值。
3. 严格校验表头、数据类型、允许的平台、正整数数量和非负成本。
4. 检查文件内部是否出现重复主键：
   - 内容完全一致时折叠为一条；
   - 内容冲突时整批拒绝，并报告 Excel 行号。
5. 完成全部校验后，在一个 SQLite 事务内执行 upsert。
6. 输出读取行数、新增数、更新数、折叠重复数、日期范围和人民币成本合计。

任何一行失败时整批不写入。错误信息不得包含凭证或其他无关环境变量。

## 成本汇总与广告分析

订单成本已经是人民币，不能写入旧 `product_costs` 后再乘汇率。
系统按以下键实时汇总：

`(platform, store, order_date)`

汇总值：

`SUM(line_cost_cny)`

广告利润分析按以下安全规则使用订单成本：

1. 同平台、同店铺、同日期只有一条 `daily_ad_metrics` 时，将当日订单
   成本作为该汇总行的商品成本人民币。
2. 同日没有广告指标时，保留订单成本，等待后续采集后自动可用。
3. 同日存在多条广告指标时，不自动平均或重复挂载；产生
   `ambiguous_order_cost_allocation` 数据提示，并继续保留成本明细。
4. 后续取得订单级广告归因或平台商品 ID 映射后，再扩展精确分摊；首期不猜测。

利润计算需要区分币种：

- GMV、退款、佣金、广告费、商家运费、优惠和固定成本沿用平台币种，
  通过每日汇率转换成人民币；
- `order_cost_lines` 汇总商品成本直接使用人民币，不再次换汇；
- 盈亏平衡 ROAS 在统一成人民币后计算。

订单成本导入只解决 `product_cost`。佣金、库存、汇率、Campaign 起始日期和
目标 ROAS 仍按各自真实数据源补齐；缺失时降低分析完整度，不伪造默认值。

## 文件支持

- CSV 使用 `utf-8-sig`，兼容中文 BOM。
- XLSX 使用项目依赖 `openpyxl>=3.1,<4` 的只读、`data_only` 模式读取，
  不依赖 Excel 应用已打开。
- 订单号和 SKU 始终按文本读取，避免前导零丢失。
- 日期统一写入 ISO `YYYY-MM-DD`。
- 金额使用 `Decimal`，禁止二进制浮点参与入库和汇总。

## CLI 与报告

导入成功示例：

```text
Imported order costs: read=9 inserted=9 updated=0 deduplicated=0
date_range=2026-07-08..2026-07-17 total_cost_cny=75.00
```

新增只读汇总命令：

```bash
.venv/bin/adwatch business order-summary \
  --from 2026-07-08 --to 2026-07-17
```

输出按日期、平台和店铺汇总的订单数、规格件数和商品成本人民币，用于人工对账。

店铺名与紫鸟内部显示名不同时，使用显式映射命令：

```bash
.venv/bin/adwatch business map-store \
  --platform shopee \
  --source no4kud44da \
  --target 虾皮泰国
```

目标店铺必须已经存在于 `daily_ad_metrics`；错误拼写会被拒绝。利润分析使用
`canonical_store` 关联广告指标，但订单明细保留原始 `source_store`。

上线检查中的 `business_costs` 在至少存在一条有效 `order_cost_lines`
或旧 `product_costs` 时视为完成。

## 测试与验收

自动化测试至少覆盖：

- 中文表头 CSV 与 XLSX 均可导入；
- `YYYYMMDD`、ISO 和 Excel 日期可正确规范化；
- 同一订单多个 SKU 可同时保存；
- 同一订单相同 SKU 的幂等更新；
- 店铺别名只能映射到同平台已经采集到的目标店铺；
- `no4kud44da` 可通过别名匹配 `虾皮泰国`；
- 文件内完全重复折叠、冲突重复整批拒绝；
- 缺列、空值、非法平台、零/负数量、负成本整批拒绝；
- 订单号和 SKU 的文本值不被数值化；
- 人民币成本不会被汇率二次换算；
- 多广告行时不重复分摊成本；
- 上线清单能识别订单成本已经存在。

首个现场验收：

- 导入 `订单SKU成本明细模板-shopee2.xlsx`；
- 读取 9 行、9 个订单、9 个规格件；
- 日期范围为 `2026-07-08` 至 `2026-07-17`；
- 人民币商品成本合计为 `75.00`；
- 第二次导入不增加行数，总成本仍为 `75.00`；
- `ADWATCH_LIVE_WRITES=false` 保持不变。

## 非目标

本次不实现：

- 自动删除源文件中已移除的订单；
- 将 1/3/5 bags 拆成库存基础单位；
- 退款、取消订单和售后成本冲销；
- 多 Campaign 间按订单归因自动分摊；
- 开启任何真实广告写操作。

这些能力在获得可靠的订单状态、平台商品 ID 和归因字段后独立设计。
