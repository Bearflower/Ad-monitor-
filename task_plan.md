# 当前任务计划

## 当前任务：修复利润重算数据链路（2026-08-10）

### 目标

统一退款 T+3 关账、订单成本快照覆盖和库存页 KPI 口径，使利润重算只在输入完整时生成结果，并能准确解释仍待补的数据。

### 阶段

- [complete] R1：生产数据与代码链路根因取证
- [complete] R2：确认业务口径并评审修复设计
- [complete] R3：编写精细 TDD 实施计划
- [complete] R4：按 RED-GREEN-REFACTOR 实施
- [complete] R5：本地全量验证与代码审查
- [complete] R6：无 `.env` 打包、服务器备份、重建部署与生产验收
- [complete] R7：追踪历史订单成本缺口并安全复用既有 SKU 成本

### 已确认边界

- 无退款/退货事实的经营日经过 T+3 后，可将退款金额关账为来源明确的 0。
- 只要存在 `returned/refund` 状态但缺真实退款金额，该日继续待补，不用订单实付额猜算。
- 不启用 TikTok，不改真实广告写入边界；保留工作区现有用户改动。
- 用户已授权上传不含 `.env` 的修复源码包到 `root@43.156.242.184`，并重建部署 `adwatch` 容器。

### R4 当前任务进度

- [complete] Task 1：迁移 29、店铺默认履约历史和每日退款事实表
- [complete] Task 2：T+3 退款关账服务
- [complete] Task 3：混合履约与订单成本覆盖
- [complete] Task 4：分析 lineage 与重算接入
- [complete] Task 5：库存读模型与页面 KPI
- [complete] Task 6：全量验证、打包、部署和生产验收
- [complete] Task 7：识别重复订单行、修正覆盖口径并回填真实缺口

## 当前主任务：完成 AdWatch 剩余功能闭环（2026-08-04）

### 目标

按已批准设计和总路线图连续完成 24 项可实施差距；TikTok 真实采集保持产品决策后置。每批均完成需求/架构核对、TDD、代码检测、全量测试、代码审查、文档更新和真实只读验收。P1-C 后准备只读服务器发布，P3 完成正式生产验收。

### 总进度

- [complete] P1-A：Shopee 退款 T+3、物流和平台库存事实
- [complete] P1-B：Income PDF、结算、平台费和广告充值
- [complete] P1-C：利润回算、经营页和数据可信度
- [complete] P1-D：财务、库存和分润 Web 闭环
- [pending] P2-A：Shopee Campaign/SKU 广告明细与分页
- [complete] P2-B：策略规则维护与审批审计页面
- [complete] P2-C：周报、月报和每周调度
- [pending] P2-D：实验体系与备用采集
- [pending] P3：上线清单、服务器部署和生产验收
- [pending] Final：重新机械审计 45 项矩阵并更新完成结论

### P1-A 开发关卡

- [complete] 需求分析与总体架构确认
- [complete] 精细 TDD 实施计划
- [complete] 编码实现
- [complete] 代码检测
- [complete] 强制测试
- [complete] 代码审查与文档对照
- [complete] 文档更新
- [complete] 真实只读验收

### P1-A 精细任务

- [complete] A1：冻结无 PII 的 Shopee 订单事实契约
- [complete] A2：增加迁移 21 和统一运行计数
- [complete] A3：实现退款、物流和库存事实仓储
- [complete] A4：把订单行和商品 SKU 转换为去重观测
- [complete] A5：扩展 ShopeeOrderSync 持久化和运行记录
- [complete] A6：配置 T+3 回溯并接入每天 09:00
- [complete] A7：扩展 CLI 计数和安全输出
- [complete] A8：真实 Shopee 只读回溯与幂等验收
- [complete] A9：文档、审查和完整质量门禁

### 持续边界

- 不启用 TikTok，不启用真实广告写入。
- 未经用户明确要求，不提交、不推送。
- 服务器部署使用 `.trae/rules` 与【服务器自动化部署】技能；任何破坏性清理先确认。
- 普通技术取舍不中断；只有权限、真实写入、不可逆数据操作或业务口径冲突时请求用户确认。

### P1-B 精细任务

- [complete] B1：冻结脱敏 Income 字段与对平契约
- [complete] B2：迁移 22、不可变版本和费率选择仓储
- [complete] B3：Shopee PDF 解析器
- [complete] B4：导入隔离与结算/费用/广告充值事实
- [complete] B5：配置及 `income import|scan|list` CLI
- [complete] B6：接入每天 09:00 非阻塞扫描
- [complete] B7：利润使用实际费率并保存血缘
- [complete] B8：日报展示费率周期和来源
- [complete] B9：真实 PDF、审查、文档和完整质量门禁

### P1-C 精细任务

- [complete] C1：迁移 24 与退款输入可信状态
- [complete] C2：利润输入 lineage 与安全降级
- [complete] C3：日期范围重算与差异报告
- [complete] C4：读取模型与日报可信度
- [complete] C5：今日经营页来源穿透
- [complete] C6：真实 30 天重算与幂等验收
- [complete] C7：文档、审查和完整质量门禁

### P1-D 精细任务

- [complete] D0：现状核对与精细 TDD 计划
- [complete] D1：迁移 25 与旧库升级
- [complete] D2：凭证仓储、校验与安全下载
- [complete] D3：账本确认、调整和全对象冲销
- [complete] D4：采购与人工库存调整/冲销
- [complete] D5：分润支付和期间冲销
- [complete] D6：余额快照、三方差额与修复链
- [complete] D7：专属页面、路由和文件传输
- [complete] D8：文档、审查、生产备份与完整质量门禁

## 当前任务：审校并定稿剩余开发计划（2026-08-04）

### 目标

以根目录 `findings.md` 的最终审计口径为准，复核并完善
`docs/plans/2026-08-04-adwatch-remaining-gap-closure.md`，确保剩余 25 项能力缺口全部有明确批次、依赖、文件边界、TDD 步骤、验收门槛和部署边界。

### 阶段

- [complete] R1：恢复规划上下文并读取项目规则
- [complete] R2：逐条映射 findings 缺口与现有计划任务
- [complete] R3：核对计划中的代码路径、测试路径和命令
- [complete] R4：修订计划并执行占位符、覆盖率和一致性复核
- [complete] R5：更新进度并提交实施顺序建议

### 边界

- 本轮只修改规划文档，不修改业务代码、不部署、不提交、不推送。
- 保留当前工作区全部已有改动。
- TikTok 继续禁采；服务器部署仅作为后续独立阶段，不在本轮执行。

### 待确认决策

- [complete] 总路线优先策略：业务数据真实性优先。
- [complete] 服务器发布：P1-C 后只读经营版、P3 后正式生产版。

## 新任务：实施两个 P0 并制定剩余开发计划（2026-08-04）

### 目标

1. 增加 Shopee 订单页与商品页真实只读同步，支持日期范围、幂等写入、失败隔离和运行记录。
2. 将订单/商品同步接入每日 09:00 流程，并保持 TikTok 禁采、真实广告写入关闭。
3. 修复 `/inventory`：展示按订单日期缺少有效成本版本的 SKU，并能在本页录入成本、履约和采购。
4. 在代码验证完成后补跑缺失订单事实，验证待补清单；不重复发送飞书。
5. 根据 `findings.md` 输出剩余 29 项的分批开发计划。

### 开发流程

- [complete] P0-1：需求分析（沿用已确认的半自动成本与统一经营设计）
- [complete] P0-2：架构设计与现有实现边界核对
- [complete] P0-3：TDD 实现 Shopee 订单/商品同步与 CLI
- [complete] P0-4：TDD 接入每日流程和故障状态
- [complete] P0-5：TDD 修复 `/inventory` 查询与页面写入闭环
- [complete] P0-6：代码规范检测、强制测试和生产只读验收
- [complete] P0-7：代码审查、文档对照与文档更新
- [complete] P0-8：根据 `findings.md` 制定剩余开发计划
- [skipped] P0-9：服务器部署（用户本次未要求部署）

### 实施边界

- 唯一代码源为当前 workspace；不读取或复制旧 Documents 项目代码。
- 订单和商品采集只允许页面导航、读取与本地 SQLite 写入，不点击平台业务操作按钮。
- 所有新行为先写失败测试并确认 RED，再写最小实现。
- 保留当前工作树全部用户改动，不提交、不推送、不覆盖无关文件。
- 真实补跑默认 `--no-notify`；不启用 TikTok，不开启 Live。

## 新任务：全项目实现差距审计（2026-08-04）

### 目标

以当前唯一代码源为准，对照 README、蓝图、设计文档和实施计划，统计：

1. 已完整实现且已接入运行流程的功能。
2. 只实现局部代码、但未接入 CLI/定时任务/页面的半成品。
3. 文档已承诺、当前代码完全未实现的功能。
4. 文档与当前实现互相矛盾或已过期的内容。
5. 给出按业务影响排序的修复批次，优先覆盖 `/inventory` 待补成本问题。

### 审计阶段

- [complete] A1：建立文档承诺清单与模块边界
- [complete] A2：逐模块核对代码、CLI、页面、迁移与测试
- [complete] A3：核对定时流程和真实数据库覆盖情况
- [complete] A4：形成缺失/半实现/已实现矩阵与优先级
- [complete] A5：复核统计证据并汇报

### 审计边界

- 本阶段只读审计，不修改业务代码、不部署、不提交。
- 以当前主工作区代码为事实来源，不以旧 Documents 目录或旧 worktree 作为可运行来源。
- “测试文件存在”不自动等同于功能已接入生产流程。
- “设计/计划存在”不自动等同于功能已实现。

## 目标

在不增加 Dashboard 自启动的前提下：

1. 确认并补跑当前缺失的 Shopee 店铺日报数据（TikTok 继续禁采）。
2. 以数据库和代码证据定位“待补 SKU 成本”为空的真实原因。
3. 验证补跑结果，并给出待补成本问题的准确结论；未经确认不扩大页面口径。

## 阶段

- [complete] 阶段 1：读取项目规则，盘点调度、数据日期与采集状态
- [complete] 阶段 2：检查紫鸟 Bridge，补跑缺失的 Shopee 日期
- [complete] 阶段 3：追踪订单 SKU → 成本历史 → 页面查询的数据流
- [complete] 阶段 4：验证数据库、报告与页面结果

## 边界

- 不实现 Dashboard 开机自启动。
- 不启用 TikTok 采集。
- 手动补跑默认不发送飞书，避免重复通知。
- 当前“再看看”按诊断处理；发现明确代码缺陷后再决定是否需要改动。

## 错误记录

| 错误 | 次数 | 处理 |
|---|---:|---|
| 无 | 0 | — |
| 误用了完整日报流程，短暂影响页面观感 | 1 | 已停止；确认数据已入库，后续仅使用采集命令 |
| 本机未安装 `pandoc`，无法直接提取 DOCX | 1 | 改用 macOS `textutil -convert txt -stdout` 只读提取 |
| 首次生产库日期汇总误用 `inventory_snapshots.data_date` | 1 | 先查表结构，再按真实日期字段重跑 |
| 二次汇总误用 `platform_sku_mappings.last_seen_on` 和 selector 激活列名 | 1 | 已停止猜列名，先读取两张表完整 schema |
| 指标明细查询误用 `daily_ad_metrics.gmv/orders` | 1 | 读取实际 schema 后改用 `attributed_gmv/attributed_orders` |
| 运行明细查询误用 `collection_runs.data_date` 与 `alerts.created_at` | 1 | collection 日期从时间/质量报告核对，告警改按 `updated_at` 排序 |
| 全量 pytest 有 2 个失败 | 1 | 记录为当前实现缺口；本轮审计不擅自改业务代码 |
| Ruff 检查发现 22 个问题 | 1 | 记录代码质量状态并定位来源，不自动格式化用户改动 |
