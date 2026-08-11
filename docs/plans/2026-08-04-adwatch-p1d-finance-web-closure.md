# AdWatch P1-D 财务、库存和分润 Web 闭环实施计划

> **执行要求：** 使用 `executing-plans`、`test-driven-development`、
> `requesting-code-review` 和 `verification-before-completion`。每个任务先确认 RED，
> 再做最小实现；未经用户明确授权不提交、不推送、不部署。

## 1. 目标与边界

在现有 SQLite 经营账、库存台账和分润服务上补齐人工维护闭环，不创建第二套
财务事实源：

- 费用草稿可在 Web 确认和冲销；出资、提款、广告资金、刷单成本和采购可冲销。
- 凭证只允许 PDF/JPEG/PNG，保存安全文件名、SHA-256、大小和关联对象；下载不能
  穿越受管目录，日志不输出原文件内容或绝对路径。
- 记录银行、平台和现金余额快照，并与账本余额形成可解释差额和修复记录。
- 收入调整、人工库存调整和分润支付使用追加/冲销，不物理删除历史事实。
- `/ad-funds`、`/inventory`、`/profit-sharing` 提供对应受控入口；所有 POST 使用
  CSRF、服务层校验、固定 `local-web` actor、原因和 `audit_events`。

不在本批做服务器部署、Live 广告写入、TikTok 采集、银行 API 或 ERP 连接器。
生产验收只迁移、读取和渲染真实库；不写入虚构财务数据。

## 2. 已确认基线

- `LedgerService` 已有费用草稿、确认和冲销，其他人工账本对象只有创建。
- `InventoryService` 已有采购、销售、退货和报损，但采购/报损缺可审计冲销，
  也没有正负人工盘点调整。
- `ProfitSharingService` 已有协议、期间确认和付款；当前允许直接创建
  `status=reversed`，没有基于原付款的冲销链。
- `DashboardRouter` 有 CSRF，但未接费用确认/冲销、凭证、余额、调整和各类冲销；
  异常捕获也未覆盖库存/分润领域错误。
- `/ad-funds` 和 `/profit-sharing` 主要只读；写表单集中在 `/operations`。
- 当前最新 schema 为 24，全量基线为 `375 passed`。

## 3. 数据模型与不变量

迁移 25 增加：

1. `evidence_documents`：随机 ID、对象类型/ID、安全展示名、受管存储名、
   `media_type`、大小、SHA-256、备注、actor、时间。数据库不保存绝对路径。
2. `balance_snapshots`：`cash|platform|bank`、账户名、平台/店铺、快照日、CNY
   金额、来源、备注、actor、时间；同一账户同日可追加修订，最新一条生效。
3. `balance_reconciliations`：快照日、账本现金、三类最新观测值、逐项差额、状态、
   解决调整 ID、actor 和时间；历史运行不可覆盖。
4. `financial_adjustments`：`income|cash|balance`、带符号金额、发生日、原因、状态、
   `reversal_of`、actor 和时间；冲销必须新增相反金额并指回原调整。
5. 为 `inventory_movements` 增加 actor、原因和 `reversal_of`；为
   `profit_payments` 增加 actor、冲销原因和 `reversal_of`。旧数据以 `system`
   回填，不伪造凭证。

共同不变量：

- 金额使用 `Decimal` 字符串，不用 SQLite `REAL` 做业务累计。
- 原始确认事实不可删除；状态变化必须与反向现金/库存事实和审计事件同事务。
- 同一事实最多冲销一次；冲销不能再次冲销。
- 余额快照是观测，不自动冒充账本流水；差额修复必须显式创建调整。
- 收入调整纳入分润期间计算，但广告充值、采购和余额快照仍不计收入。
- 文件最大 5 MiB；内容魔数与声明类型一致；存储名由服务生成，不接受用户路径。

## 4. TDD 执行任务

### D1：迁移 25 与旧库升级

- [x] 先写新表、列、约束、索引、旧库升级和迁移原子回滚测试并确认 RED。
- [x] 验证旧库存/分润行 actor 回填，旧账本金额和状态不改变。
- [x] 验证同一调整、付款或库存事实不能被重复冲销。

Files：

- Modify: `src/adwatch/storage/migrations.py`
- Create: `tests/storage/test_finance_closure_migration.py`

Run：`.venv/bin/python -m pytest tests/storage/test_finance_closure_migration.py -q`

### D2：凭证仓储、校验与安全下载

- [x] 先覆盖 PDF/JPEG/PNG 魔数、5 MiB 上限、控制字符文件名、重复内容、路径穿越
  和未知对象测试。
- [x] 新增 `EvidenceService`，只在配置的 `var/evidence/` 内用 UUID 名原子落盘；
  数据库事务失败时清理本次临时文件。
- [x] 增加对象关联查询和受管下载读取；响应使用固定 MIME、`nosniff` 和安全
  `Content-Disposition`，不接受路径参数。
- [x] 上传/下载日志和错误只含凭证 ID/错误码，不含正文或绝对路径。

Files：

- Create: `src/adwatch/evidence/__init__.py`
- Create: `src/adwatch/evidence/service.py`
- Modify: `src/adwatch/config.py`
- Modify: `.env.example`
- Create: `tests/evidence/test_service.py`

Run：`.venv/bin/python -m pytest tests/evidence -q`

### D3：账本确认、调整和全对象冲销

- [x] 覆盖费用确认/冲销幂等与非法状态，以及出资、提款、广告资金、刷单成本的
  反向现金流水和重复冲销拒绝。
- [x] 实现收入/现金调整和反向调整；每次写入带 actor、原因和审计事件。
- [x] 查询接口返回状态、冲销关系和可关联凭证，不暴露磁盘路径。
- [x] 分润净利润读取已确认收入调整的净额，冲销后确定性恢复。

Files：

- Modify: `src/adwatch/ledger/models.py`
- Modify: `src/adwatch/ledger/service.py`
- Modify: `src/adwatch/profit_sharing/service.py`
- Modify: `tests/ledger/test_service.py`
- Modify: `tests/profit_sharing/test_service.py`

Run：`.venv/bin/python -m pytest tests/ledger tests/profit_sharing -q`

### D4：采购与人工库存调整/冲销

- [x] 覆盖正负盘点调整、负库存拒绝、采购冲销、报损冲销、重复冲销和余额方程。
- [x] `adjust_inventory()` 只接受非零数量和非空原因；所有动作写 actor 与审计。
- [x] 采购冲销在库存不足时拒绝，成功时追加反向 movement 并保留原收货单。
- [x] 销售/退货自动事实继续沿用原幂等逻辑，不被人工调整覆盖。

Files：

- Modify: `src/adwatch/inventory/service.py`
- Modify: `tests/inventory/test_service.py`

Run：`.venv/bin/python -m pytest tests/inventory -q`

### D5：分润支付和期间冲销

- [x] 禁止直接创建 `status=reversed` 的付款；只有已支付记录可以冲销一次。
- [x] 付款冲销新增相反金额记录并关联原付款；已付净额按冲销净值计算。
- [x] 已确认期间只有在没有未冲销支付时才能冲销；保留原分配和原因。
- [x] 所有分润状态变化写 before/after、actor、原因和审计事件。

Files：

- Modify: `src/adwatch/profit_sharing/service.py`
- Modify: `tests/profit_sharing/test_service.py`

Run：`.venv/bin/python -m pytest tests/profit_sharing -q`

### D6：余额快照、三方差额与修复链

- [x] 先覆盖最新快照选择、同日修订、缺任一来源的 partial、Decimal 差额和解决
  调整关联。
- [x] `BalanceReconciliationService` 从现金流水计算账本余额，并读取截至快照日的
  最新 `cash|platform|bank` 观测；逐项展示与账本差额，不偷偷相互抵消。
- [x] 差额只能由显式 `financial_adjustment` 标记解决；原对账运行保持不可变。
- [x] 读取模型区分“无快照”“有差额”“已解决”，禁止把缺数据渲染为 0。

Files：

- Create: `src/adwatch/reconciliation/balances.py`
- Modify: `src/adwatch/reconciliation/__init__.py`
- Modify: `tests/reconciliation/test_service.py`
- Create: `tests/reconciliation/test_balances.py`

Run：`.venv/bin/python -m pytest tests/reconciliation -q`

### D7：专属页面、路由和文件传输

- [x] `DashboardRouter` 增加费用确认/冲销、账本冲销、收入调整、余额快照/解决、
  库存调整/冲销、分润付款/期间冲销路由；统一捕获各领域错误并返回 400。
- [x] `/ad-funds` 放入费用草稿/确认/冲销、收入调整、广告资金和三方余额表单；
  `/inventory` 放入人工盘点和采购冲销；`/profit-sharing` 放入协议/期间/付款及
  冲销表单。页面使用真实记录 ID，不要求用户手抄 UUID。
- [x] HTTP Handler 为 `/evidence` 实现受限 multipart 上传，为 `/evidence/<id>`
  实现安全下载；其他 POST 仍保持 1 MiB URL 编码限制。
- [x] 页面文本 escape、所有表单有 CSRF、危险动作有原因字段；响应式 CSS 禁止
  页面级横向溢出，宽表只在自身容器滚动。

Files：

- Modify: `src/adwatch/dashboard/routes.py`
- Modify: `src/adwatch/dashboard/views.py`
- Modify: `src/adwatch/dashboard/app.py`
- Modify: `tests/dashboard/test_routes.py`
- Modify: `tests/dashboard/test_views.py`
- Modify: `tests/dashboard/test_app.py`

Run：`.venv/bin/python -m pytest tests/dashboard -q`

### D8：文档、独立审查与真实只读验收

- [ ] 更新 README、数据底座、`findings.md`、总计划、主任务和进度。
- [ ] 独立审查并修复全部 Critical/Important。
- [ ] 全量 pytest、Ruff、compileall、`git diff --check`。
- [ ] 生产迁移前在线备份并校验；迁移后只读验证 schema、完整性、旧表数量/金额、
  页面渲染和凭证根目录权限。不写入虚构财务记录、不发送飞书。
- [x] 在临时验收库完成：费用创建→确认→凭证→冲销、余额快照→差额→调整解决、
  库存调整→冲销、分润付款→冲销的端到端双运行验收。
- [ ] 在允许运行本地端口的环境完成桌面和 375px 真实浏览器截图/滚动验收；当前
  沙箱端口绑定因工具额度被拒绝，不能用静态检查冒充该项通过。

## 5. 完成门槛

- 所有人工财务/库存/分润写入有 actor、时间、原因（适用时）、审计和可选凭证哈希。
- 所有确认财务事实只能追加或冲销，不能物理删除；重复提交不产生第二次反向事实。
- 三方余额缺输入时明确 partial，有输入时差额可穿透到快照与解决调整。
- 三个专属页面可完成创建、确认/冲销和查询，不再依赖通用 `/operations` 页面。
- 凭证下载不能路径穿越、猜测本机路径或执行主动内容。
- 全量门禁和独立审查通过，生产旧数据零漂移后才进入 P2-A。
