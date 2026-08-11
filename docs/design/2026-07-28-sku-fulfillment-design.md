# SKU 履约模式与混合库存设计

日期：2026-07-28

## 目标

履约方式只归属于 SKU，不归属于店铺。一个店铺可以同时销售货盘代发
SKU 和自有备货 SKU；同一 SKU 未来也可以按生效日期从货盘切换为
自有备货，且不会重写历史订单。

## 数据模型

- `sku_fulfillment_history`：平台、店铺、Seller SKU、生效日期、履约
  模式、供货状态、备注、创建时间。履约模式仅允许
  `supplier_fulfilled`（货盘代发）和 `stocked`（自有备货）。
- `order_fulfillment_snapshots`：平台、店铺、订单号、Seller SKU、
  履约模式、生效日期、解析来源和创建时间。
- 履约历史的唯一键为平台、店铺、Seller SKU、生效日期。
- 订单快照的唯一键为平台、店铺、订单号、Seller SKU。

订单按下单日期选取最近一条已生效的 SKU 履约记录，并把结果冻结。
以后新增履约版本不会修改已有订单快照。

## 业务行为

### 货盘代发

- 不要求采购、期初库存或库存数量。
- 有效订单直接按订单日期匹配 `sku_cost_history` 并生成已确认的订单
  成本快照。
- 不生成 `inventory_movements` 或 `inventory_balances`。
- 取消订单不生成成本；全额退货订单的成本快照标记为 `returned`。
- 广告策略的库存能力为 `not_applicable`，不得因缺库存数据阻塞分析。
- 供货风险使用 SKU 的 `available`、`paused` 状态，而不是库存件数。

### 自有备货

- 继续使用采购入库、销售出库、退货回库和库存余额。
- 库存不足时订单保持 `pending_inventory`，不制造负库存。
- 广告策略继续使用库存数量和预计日销量作为加预算门禁。

### 缺少履约方式

- 订单保持 `pending_fulfillment`。
- 不猜测货盘或库存模式，不生成成本或库存移动。

## 当前数据迁移

现有 `no4kud44da` 店铺的 65 个 Seller SKU 全部新增一条
`supplier_fulfilled` 履约记录，生效日期采用该 SKU 已有成本历史的最早
生效日期。现有 175 个订单按订单日期生成履约快照；24 条取消明细跳过，
其余订单行生成成本快照而不生成库存流水。

迁移必须幂等。重复执行不得重复履约记录、订单快照或订单成本。

## Web 与 CLI

- Web 的 SKU 区域增加履约模式、生效日期、供货状态和备注写入。
- CLI 增加 `business set-fulfillment` 用于单 SKU 设置。
- CLI 增加 `business mark-current-skus-supplier-fulfilled`，批量把当前已有
  SKU 按最早成本日期标记为货盘代发。
- `business sync-orders` 输出 `pending_fulfillment`、货盘成本处理数和
  自有库存出库数。

## 安全与审计

- 不删除历史履约版本。
- 已冻结订单快照不随 SKU 配置变化而改变。
- 单位成本缺失时保持 `pending_cost`，不写零成本。
- 财务成本与现金付款保持分离；货盘成本进入订单利润，但供应商付款由
  现金账另行记录。

## 验收

- 当前 65 个 SKU 均为货盘代发。
- 现有有效订单不再出现 `pending_inventory`。
- 有效订单成本覆盖率为 100%，取消订单不计成本。
- 库存流水仍为 0。
- 新增自有备货 SKU 的原库存流程保持不变。
