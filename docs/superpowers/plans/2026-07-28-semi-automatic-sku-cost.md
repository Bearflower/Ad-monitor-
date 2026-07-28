# Semi-Automatic SKU Cost Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 自动同步 Shopee 订单与商品事实，生成只需填写人民币单件成本的待补 SKU 工作簿，并输出自动匹配成本的订单经营明细。

**Architecture:** 新增订单事实、平台 SKU 和成本历史三组持久化表。紫鸟页面适配器只负责把页面文本解析为领域记录，存储服务负责幂等写入和成本匹配，XLSX 服务负责中文工作簿边界，CLI 只做参数编排。现有人工 `order_cost_lines` 保留并在订单级成本匹配时优先。

**Tech Stack:** Python 3.11、SQLite、openpyxl、argparse、pytest、紫鸟 CLI。

---

## 文件结构

- `src/adwatch/storage/migrations.py`：迁移 9，新增订单事实、平台 SKU、成本历史与同步运行表。
- `src/adwatch/orders/models.py`：订单行、平台 SKU、同步结果数据类。
- `src/adwatch/orders/shopee_parser.py`：解析 Shopee 订单页与商品页的结构化文本。
- `src/adwatch/orders/repository.py`：幂等写入、查询缺成本 SKU、历史成本匹配。
- `src/adwatch/orders/ziniao_sync.py`：调用固定紫鸟页面导航/读取动作并编排解析。
- `src/adwatch/analytics/sku_cost_workbook.py`：导出/导入待补成本 XLSX。
- `src/adwatch/reporting/order_ledger.py`：导出只读订单经营明细 XLSX。
- `src/adwatch/cli.py`：新增 `orders sync`、成本表和订单明细命令。
- `tests/orders/`：解析、存储、同步测试。
- `tests/analytics/test_sku_cost_workbook.py`：成本表测试。
- `tests/reporting/test_order_ledger.py`：经营明细测试。
- `tests/test_order_sync_cli.py`：CLI 测试。

### Task 1：迁移 9 与领域模型

**Files:**
- Modify: `src/adwatch/storage/migrations.py`
- Create: `src/adwatch/orders/__init__.py`
- Create: `src/adwatch/orders/models.py`
- Create: `tests/storage/test_order_sync_migration.py`

- [ ] **Step 1: 写迁移失败测试**

断言迁移后存在 `platform_order_lines`、`platform_sku_mappings`、
`sku_cost_history`、`order_sync_runs`，并验证订单行和成本历史唯一键。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/storage/test_order_sync_migration.py -q`

Expected: FAIL，缺少迁移 9 表。

- [ ] **Step 3: 实现迁移和不可变模型**

`PlatformOrderLine` 包含设计文档中的订单字段；`PlatformSku` 包含商品标识、
库存和采集时间；金额使用 `Decimal`，日期使用 `date`，状态保留平台原值。

- [ ] **Step 4: 运行迁移测试并确认 GREEN**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/storage/test_order_sync_migration.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/adwatch/storage/migrations.py src/adwatch/orders tests/storage/test_order_sync_migration.py
git commit -m "feat: add order and SKU cost storage"
```

### Task 2：Shopee 页面解析器

**Files:**
- Create: `src/adwatch/orders/shopee_parser.py`
- Create: `tests/orders/test_shopee_parser.py`

- [ ] **Step 1: 写订单解析失败测试**

用脱敏固定页面文本覆盖：同订单多 SKU、同 SKU 多件、To Ship、Shipped、
Cancelled、Return/Refund、物流单号缺失。断言订单状态和物流状态分别保存，
取消和退款不会在解析阶段被删除。

- [ ] **Step 2: 运行订单解析测试并确认 RED**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/orders/test_shopee_parser.py -q`

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现纯函数 `parse_order_page(text, store, observed_at)`**

按 `Order ID` 分块，按商品名、`Variation:`、`x数量` 和订单状态解析；无法
唯一识别的块返回带原因的 rejected 记录，禁止猜测。

- [ ] **Step 4: 写并运行商品解析失败测试**

覆盖 Item ID、Seller SKU、Model ID、规格、库存和 Sold out。

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/orders/test_shopee_parser.py -q`

Expected: 商品解析用例 FAIL。

- [ ] **Step 5: 实现 `parse_product_page` 并确认 GREEN**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/orders/test_shopee_parser.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src/adwatch/orders/shopee_parser.py tests/orders/test_shopee_parser.py
git commit -m "feat: parse Shopee orders and products"
```

### Task 3：订单与 SKU 存储服务

**Files:**
- Create: `src/adwatch/orders/repository.py`
- Create: `tests/orders/test_repository.py`

- [ ] **Step 1: 写幂等同步失败测试**

同一订单重复同步只保留一行；状态、物流和库存使用新观察值更新，已有非空值
不会被空字符串覆盖。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/orders/test_repository.py -q`

Expected: FAIL，存储服务不存在。

- [ ] **Step 3: 实现 `OrderRepository.upsert_orders/upsert_skus`**

全部写入使用单事务；返回插入、更新和拒绝计数。

- [ ] **Step 4: 写成本匹配失败测试**

断言按订单日期选择最近且不晚于订单日的 SKU 成本；存在旧版
`order_cost_lines` 时订单级成本优先；取消订单成本为零；处理中退款保留
成本但标记收入未确认。

- [ ] **Step 5: 实现查询并确认 GREEN**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/orders/test_repository.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src/adwatch/orders/repository.py tests/orders/test_repository.py
git commit -m "feat: persist and match platform orders"
```

### Task 4：待补 SKU 成本工作簿

**Files:**
- Create: `src/adwatch/analytics/sku_cost_workbook.py`
- Create: `tests/analytics/test_sku_cost_workbook.py`

- [ ] **Step 1: 写导出失败测试**

断言中文表头、冻结首行、自动筛选、成本输入列高亮、已有有效成本 SKU 排除、
同 SKU 多订单聚合为一行、当前库存和待匹配数量正确。

- [ ] **Step 2: 运行导出测试并确认 RED**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/analytics/test_sku_cost_workbook.py -q`

Expected: FAIL，导出函数不存在。

- [ ] **Step 3: 实现 `export_pending_sku_costs`**

使用 openpyxl 创建 `待补成本` 工作表；只允许
`单件成本_人民币`、`成本生效日期`、`成本备注` 为用户输入列。

- [ ] **Step 4: 写导入失败测试**

覆盖空成本跳过、负数/非数字拒绝、Seller SKU 缺失拒绝、整批回滚、重复导入
幂等更新和不同生效日期保存历史。

- [ ] **Step 5: 实现 `import_sku_costs` 并确认 GREEN**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/analytics/test_sku_cost_workbook.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src/adwatch/analytics/sku_cost_workbook.py tests/analytics/test_sku_cost_workbook.py
git commit -m "feat: add pending SKU cost workbook"
```

### Task 5：订单经营明细工作簿

**Files:**
- Create: `src/adwatch/reporting/order_ledger.py`
- Create: `tests/reporting/test_order_ledger.py`

- [ ] **Step 1: 写明细导出失败测试**

断言中文列、订单/物流/退款状态、成本来源、单件和总成本、取消订单成本处理、
成本缺失标记以及日期范围过滤。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/reporting/test_order_ledger.py -q`

Expected: FAIL，导出函数不存在。

- [ ] **Step 3: 实现 `export_order_ledger`**

创建只读风格 `订单经营明细` 工作表；成本缺失行使用黄色，退款处理中使用
橙色，取消订单使用灰色。

- [ ] **Step 4: 运行测试并确认 GREEN**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/reporting/test_order_ledger.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/adwatch/reporting/order_ledger.py tests/reporting/test_order_ledger.py
git commit -m "feat: export order operating ledger"
```

### Task 6：紫鸟只读同步编排

**Files:**
- Create: `src/adwatch/orders/ziniao_sync.py`
- Modify: `src/adwatch/collectors/ziniao_client.py`
- Create: `tests/orders/test_ziniao_sync.py`
- Modify: `tests/collectors/test_ziniao_client.py`

- [ ] **Step 1: 写固定动作失败测试**

断言同步器只调用订单页/商品页导航和页面内容读取，不调用 click、input 或任意
写操作；页面失败时记录失败运行且不清空旧数据。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/orders/test_ziniao_sync.py tests/collectors/test_ziniao_client.py -q`

Expected: FAIL，同步接口不存在。

- [ ] **Step 3: 实现 `ZiniaoOrderSync`**

接收客户端、仓库和 Shopee store ID；读取页面后调用纯解析器并写入，返回
同步计数和拒绝原因。

- [ ] **Step 4: 运行测试并确认 GREEN**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/orders/test_ziniao_sync.py tests/collectors/test_ziniao_client.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/adwatch/orders/ziniao_sync.py src/adwatch/collectors/ziniao_client.py tests/orders/test_ziniao_sync.py tests/collectors/test_ziniao_client.py
git commit -m "feat: sync Shopee order facts through Ziniao"
```

### Task 7：CLI、上线检查与每日任务集成

**Files:**
- Modify: `src/adwatch/cli.py`
- Modify: `src/adwatch/operations/launch_checklist.py`
- Create: `tests/test_order_sync_cli.py`
- Modify: `tests/operations/test_launch_checklist.py`
- Modify: `tests/test_daily_run_cli.py`

- [ ] **Step 1: 写 CLI 失败测试**

覆盖 `orders sync`、`business export-pending-sku-costs`、
`business import-sku-costs`、`business export-order-ledger` 的参数和退出码。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/test_order_sync_cli.py -q`

Expected: FAIL，命令不存在。

- [ ] **Step 3: 实现 CLI 编排**

所有写数据库命令先迁移；XLSX 输出创建父目录；错误输出明确行号且返回 2。

- [ ] **Step 4: 集成每日只读同步与上线检查**

每日任务在广告采集前同步订单事实；订单同步失败只标记 partial，不影响广告
报告。存在平台 SKU、库存和退款同步证据时分别关闭对应上线待办。

- [ ] **Step 5: 运行相关测试并确认 GREEN**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/test_order_sync_cli.py tests/test_daily_run_cli.py tests/operations/test_launch_checklist.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src/adwatch/cli.py src/adwatch/operations/launch_checklist.py tests/test_order_sync_cli.py tests/test_daily_run_cli.py tests/operations/test_launch_checklist.py
git commit -m "feat: expose semi-automatic cost workflow"
```

### Task 8：文档、全量验证与真实模板生成

**Files:**
- Modify: `README.md`
- Modify: `docs/blueprint-gap-audit-2026-07-27.md`

- [ ] **Step 1: 更新用户工作流文档**

记录四条命令、中文表头、成本生效规则、订单/物流状态自动采集和旧模板兼容。

- [ ] **Step 2: 运行静态与全量测试**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest -q`

Expected: 全部 PASS。

Run: `PYTHONPATH=src ../../.venv/bin/python -m ruff check src tests`

Expected: 无错误。

- [ ] **Step 3: 提交文档**

```bash
git add README.md docs/blueprint-gap-audit-2026-07-27.md
git commit -m "docs: explain semi-automatic SKU costing"
```

- [ ] **Step 4: 合并后生产验证**

在正式目录备份 SQLite，执行真实 Shopee 订单/商品同步，导出
`/Users/yl/Desktop/待补SKU成本-shopee.xlsx`，重新运行
`adwatch launch-checklist`，并确认 `ADWATCH_LIVE_WRITES=false`。
