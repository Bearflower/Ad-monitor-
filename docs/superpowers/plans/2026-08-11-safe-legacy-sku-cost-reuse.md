# 旧 SKU 成本安全继承 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 安全复用可唯一对应的历史 SKU 成本，并让库存页只展示真实、非重复的利润阻塞缺口。

**Architecture:** 在有效订单 SKU 视图中将“物理 Item/Model 数量”改为“规范商品名称+规格身份数量”，只对真实业务冲突拆键；订单成本服务增加受约束的历史成本继承，并统一店铺别名和订单状态。库存查询以有效订单身份为唯一来源，主清单聚合缺成本 SKU，已有成本但缺快照的记录作为同步异常分开处理。

**Tech Stack:** Python 3.12、SQLite 视图/迁移、openpyxl、pytest、标准库 HTTP/Docker 部署。

---

### Task 1: 锁定商品身份回归

**Files:**
- Modify: `tests/storage/test_order_sync_migration.py`
- Modify: `tests/orders/test_repository.py`

- [ ] **Step 1: 写失败测试**：构造同 Seller SKU 的旧合成映射与名称/规格相同的真实映射，断言有效订单成本键不拆分；另构造 `Chief 50ml` 与 `Chief 250ml`，断言仍生成两个带 Item/Model 的成本键。
- [ ] **Step 2: 验证红灯**：运行 `.venv/bin/python -m pytest tests/storage/test_order_sync_migration.py tests/orders/test_repository.py -q`，预期“同商品不拆分”断言失败。
- [ ] **Step 3: 最小实现**：新增 SQLite 迁移，依据规范店铺的标准化 `product_name`、`variation_name` 计算业务身份数，并让 `effective_order_sku_lines` 使用该数量决定 `sku_identity`。
- [ ] **Step 4: 验证绿灯**：重复运行上述测试，预期全部通过。

### Task 2: 锁定历史成本安全继承

**Files:**
- Modify: `tests/orders/test_operations_sync.py`
- Modify: `src/adwatch/orders/sync.py`

- [ ] **Step 1: 写失败测试**：覆盖“精确键优先”“同商品唯一匹配时继承原 Seller SKU 成本”“相同 Seller SKU 对应不同商品时拒绝继承”“别名店铺可继承”“成本生效日晚于订单日不可继承”。
- [ ] **Step 2: 验证红灯**：运行 `.venv/bin/python -m pytest tests/orders/test_operations_sync.py -q`，预期唯一匹配继承场景失败。
- [ ] **Step 3: 最小实现**：在成本选择查询中先查精确键，再通过当前订单的规范商品身份和唯一旧映射查原 Seller SKU 成本；任何多义结果返回缺成本。
- [ ] **Step 4: 验证绿灯**：重复运行测试，预期全部通过。

### Task 3: 修复快照状态和别名幂等

**Files:**
- Modify: `tests/orders/test_operations_sync.py`
- Modify: `tests/inventory/test_service.py`
- Modify: `src/adwatch/orders/sync.py`
- Modify: `src/adwatch/inventory/service.py`

- [ ] **Step 1: 写失败测试**：覆盖别名切换不重复生成快照、逐订单+SKU 数量不足不能被其他订单超额掩盖、退货快照不与同一有效订单事实形成假缺口。
- [ ] **Step 2: 验证红灯**：运行 `.venv/bin/python -m pytest tests/orders/test_operations_sync.py tests/inventory/test_service.py -q`，确认对应断言失败。
- [ ] **Step 3: 最小实现**：所有快照查询与写入先解析规范店铺；缺口查询复用 `effective_order_sku_lines` 的业务身份和有效状态，按订单+SKU 比较数量。
- [ ] **Step 4: 验证绿灯**：重复运行测试，预期全部通过。

### Task 4: 收敛待补成本页面和导出

**Files:**
- Modify: `tests/dashboard/test_app.py`
- Modify: `tests/analytics/test_sku_cost_workbook.py`
- Modify: `tests/orders/test_repository.py`
- Modify: `src/adwatch/dashboard/views.py`
- Modify: `src/adwatch/inventory/service.py`
- Modify: `src/adwatch/orders/repository.py`

- [ ] **Step 1: 写失败测试**：断言页面同一缺成本 SKU 只出现一个主条目、已有成本但缺快照显示为同步异常、从未成交 SKU 不进入待补导出。
- [ ] **Step 2: 验证红灯**：运行 `.venv/bin/python -m pytest tests/dashboard tests/analytics/test_sku_cost_workbook.py tests/orders/test_repository.py -q`，确认重复/未成交场景失败。
- [ ] **Step 3: 最小实现**：主清单使用缺成本 SKU 聚合结果；订单快照异常排除已在主清单覆盖的身份；删除 `pending_sku_costs` 中“从未有订单也列出”的分支。
- [ ] **Step 4: 验证绿灯**：重复运行测试，预期全部通过。

### Task 5: 数据回放、全量验证与部署

**Files:**
- Modify: `progress.md`
- Modify: `findings.md`

- [ ] **Step 1: 本地数据回放**：备份 `var/adwatch.sqlite3`，执行迁移、`business sync-orders` 和利润重算；记录修复前后缺成本 SKU、缺快照订单、待补利润日期。
- [ ] **Step 2: 静态与全量回归**：运行 `.venv/bin/python -m pytest -q`、`.venv/bin/python -m ruff check src tests`、`.venv/bin/python -m compileall -q src tests`，预期全部成功。
- [ ] **Step 3: 代码审查与幻觉检测**：检查 SQL 的别名、有效日期、取消/退货状态、重复 Seller SKU 边界，并执行 `rg -n "TBD|TODO|placeholder|implement later"` 检查新增文档与代码。
- [ ] **Step 4: 安全部署**：打包不含 `.env`、数据库、备份和报告的源码，上传到 `root@43.156.242.184`，备份线上数据库，重新构建 `adwatch` 容器。
- [ ] **Step 5: 线上验收**：验证容器健康、`/adwatch/` 与 `/adwatch/inventory` 返回 200；只读核对两个安全继承候选、`moxa stick-one box`、真实待补 SKU 数和利润重算结果。
