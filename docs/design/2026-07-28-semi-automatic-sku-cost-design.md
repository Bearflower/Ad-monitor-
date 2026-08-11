# Shopee 半自动 SKU 成本维护设计

## 目标

通过紫鸟 CLI 自动同步 Shopee 订单与商品事实，自动生成待补成本的 SKU
清单。用户只维护人民币单件成本，不再逐订单重复录入商品信息、订单状态或
物流状态。

## 核心原则

- 订单、商品、订单状态、物流状态和退款状态属于平台事实，由系统采集。
- 商品成本属于企业私有经营数据，由用户确认。
- 成本按 `平台 + 店铺 + Seller SKU + 生效日期` 管理，以支持历史成本。
- 不用规格显示名称作为唯一键；`1 bag` 等显示值可能在不同商品间重复。
- 未知成本保持缺失，不允许自动填零或猜测。
- 真实广告写操作继续关闭；本功能只读平台页面并写入本地 SQLite。

## 两类工作簿

### 待补 SKU 成本清单

系统导出 XLSX，自动填写：

- 平台
- 店铺
- 商品名称
- Item ID
- Model ID
- Seller SKU
- 规格
- 当前库存
- 首次发现日期
- 最近销售日期
- 待匹配订单数
- 待匹配件数
- 成本状态

用户只需填写：

- 单件成本_人民币
- 成本生效日期（默认首次发现日期，可调整）
- 成本备注（可选）

导入时按整批事务校验。任何必填值或格式错误都会拒绝整批写入；重复导入
采用幂等更新。

### 订单经营明细

系统生成只读对账工作簿，包含：

- 日期、平台、店铺、订单号
- 商品、Item ID、Model ID、Seller SKU、规格、数量
- 订单状态、物流状态、退款状态
- 买家实付、币种
- 匹配的人民币单件成本、总商品成本
- 成本匹配状态

该工作簿不作为人工输入源。

## 数据模型

### `platform_order_lines`

每个订单商品行一条记录：

- `platform`
- `store`
- `order_id`
- `item_id`
- `model_id`
- `seller_sku`
- `variation_name`
- `product_name`
- `quantity`
- `buyer_paid`
- `currency`
- `order_status`
- `logistics_status`
- `refund_status`
- `ordered_at`
- `source_updated_at`

唯一键为 `platform + store + order_id + item_id + model_id`。

### `sku_cost_history`

- `platform`
- `store`
- `seller_sku`
- `effective_date`
- `unit_cost_cny`
- `note`
- `updated_at`

唯一键为 `platform + store + seller_sku + effective_date`。

### `platform_sku_mappings`

保存平台标识与 Seller SKU 的稳定关系：

- `platform`
- `store`
- `item_id`
- `model_id`
- `seller_sku`
- `variation_name`
- `product_name`
- `inventory_units`
- `observed_at`

唯一键为 `platform + store + item_id + model_id`。

现有 `order_cost_lines` 保留，作为旧版人工订单成本导入的兼容入口。新流程
优先使用平台订单行与 SKU 成本历史；两者同时存在时，订单行明确录入的成本
优先，避免改变已确认的历史数据。

## CLI 工作流

```bash
adwatch orders sync --platform shopee --from YYYY-MM-DD --to YYYY-MM-DD
adwatch business export-pending-sku-costs --output 待补SKU成本.xlsx
adwatch business import-sku-costs --file 待补SKU成本-已填写.xlsx
adwatch business export-order-ledger --from YYYY-MM-DD --to YYYY-MM-DD \
  --output 订单经营明细.xlsx
```

`orders sync` 使用紫鸟 CLI 页面能力，只执行导航、查询和读取，不点击订单或
物流操作按钮。

## 状态处理

- 订单状态和物流状态每次同步均可更新。
- 取消订单不计商品成本和有效订单。
- 退货退款中的订单标记为处理中，不提前确认净收入。
- 已退款订单记录退款金额，并从验证利润中扣除。
- 页面暂时不可用时保留上次状态，同时记录同步失败，不覆盖成空值。
- 页面显示状态与广告归因订单数不同属于口径差异，保留双方事实用于对账。

## 页面采集与可维护性

- 订单页和商品页分别使用独立解析器。
- 原始页面证据保存摘要、采集时间和页面 URL，便于选择器失效时排查。
- 页面字段无法唯一解析时将记录送入隔离区，不猜测合并。
- 首期覆盖 Shopee；接口边界保留 TikTok 扩展能力。

## 测试与验收

- 数据库迁移及唯一键测试。
- 订单状态、物流状态、退款状态解析测试。
- 同订单多 SKU、同 SKU 多件、取消、退款和重复同步测试。
- 待补成本清单去重、已维护 SKU 排除和历史成本生效日期测试。
- XLSX 中文表头、空成本、非法成本和整批回滚测试。
- 订单经营明细成本匹配优先级测试。
- Shadow 真实页面验收：同步结果与 Shopee 页面抽查一致。
- 全量自动化测试通过后，才接入每天 08:00 的现有任务。

## 当前已确认的真实映射

Shopee 店铺 `虾皮泰国`：

- Item ID：`57861884313`
- `Foot Soak Bag-one bag` / Model ID `311033956020` / 规格 `1 bag`
- `Foot Soak Bag-two bags` / Model ID `311033956021` / 规格 `3 bags`
- `Foot Soak Bag-three bags` / Model ID `311033956022` / 规格 `5 bags`

已知人民币单件成本分别为 5、11、17 元。首次迁移时可从现有已确认订单成本
记录建立成本历史，但不得推断其他商品成本。
