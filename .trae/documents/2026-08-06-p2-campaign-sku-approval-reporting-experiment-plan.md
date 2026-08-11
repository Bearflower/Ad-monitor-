# P2 实施方案：Campaign/SKU 明细 · 策略审批 · 周月报 · 实验体系

> **状态**: 规划完成  
> **基准**: P1-D 已完成（430 tests passed, Ruff 0, compileall 通过）  
> **P2-A 状态**: 已完成（6.1-6.4 全部实现，含明细采集、翻页、对账、隔离）  
> **P2-B/C**: 待实现  
> **P2-D**: ✅ 已完成（9.1-9.3 全部实现，含实验体系、护栏评估、采集器故障切换）  

---

## 总览

P2 共 4 个批次（P2-A/B/C/D），15 个任务，按依赖顺序执行：

```
P2-A (已完成) → P2-B (策略审批UI) → P2-C (周月报调度) → P2-D (实验体系)
```

P2-B 和 P2-C 修改不同文件集，可并行开发。P2-D 与 P2-C 在 `daily_run.py` 上有冲突，建议串行。

---

## 批次一：P2-A — Shopee Campaign/SKU 广告明细与分页 ✅ 已完成

### 实现内容

- **6.1** 页面脚本与夹具：新增 `SHOPEE_DETAIL_PAGE_SCRIPT`（提取概览 + 表格行 + 分页信息）
- **6.2** 翻页与明细采集：修改 `ZiniaoCollector.collect()`：先获概览+第一页，循环翻页直至无下一页（最大 20 页，重试 3 次）
- **6.3** 明细存储与汇总：概览存 `__ALL__`，明细行存真实 campaign_id/sku_id；`MetricRepository` 批量 upsert
- **6.4** 对账与隔离：新增 `reconcile_ad_metrics()`：明细合计 vs 概览，超 5% 阈值进隔离；新增 `ad_reconciliation_log` 表

### 涉及文件

| 文件 | 修改类型 |
|------|---------|
| `src/adwatch/collectors/ziniao.py` | 修改：新增 `SHOPEE_DETAIL_PAGE_SCRIPT`、翻页循环、明细+概览双输出 |
| `src/adwatch/storage/metrics.py` | 新增：`reconcile_ad_metrics()`、`log_reconciliation()`、`quarantine_metrics()` |
| `src/adwatch/storage/migrations.py` | 新增：migration 26 — `ad_reconciliation_log` 表 |
| `src/adwatch/pipeline/runner.py` | 修改：集成对账逻辑，超额明细进入隔离 |
| `tests/collectors/test_ziniao.py` | 新增：明细解析测试、分页夹具测试、对账测试 |

---

## 批次二：P2-B — 策略规则维护与审批审计页面

### 当前状态

| 模块 | 状态 | 说明 |
|------|------|------|
| `/strategy-rules` 页面 | 只读 | 展示当前版本、历史版本列表、参数对比表格 |
| 策略规则 Web 表单 | 未实现 | 无"新增版本"、"生效/关闭"等表单 |
| `StrategyRuleService.create_version()` | 已实现 | 含参数校验、版本号递增、生效日期冲突检测 |
| `DashboardRouter.post()` 策略规则路由 | 未实现 | 无 `/strategy-rules/create` 等 POST 路由 |
| `/approvals` 页面 | 占位 | `render_module_page()` 显示"正在开发中" |
| `ApprovalService` | 已实现 | `create()`、`decide()` 方法，含 token 签名和过期检测 |
| 审批页面渲染 | 未实现 | `render_approvals_page()` 不存在 |
| 审计模块 | 未实现 | `src/adwatch/audit/` 目录不存在 |
| Shadow 模式 UI | 未实现 | 无相关页面入口 |

### 涉及文件

| 文件 | 修改类型 | 说明 |
|------|---------|------|
| `src/adwatch/dashboard/views.py` | 修改 | 新增 `render_approvals_page()`；`render_strategy_rules_page()` 增加创建表单 |
| `src/adwatch/dashboard/routes.py` | 修改 | `DashboardRouter` 新增 `strategy_rule`、`approval` 参数；新增 `/strategy-rules/create`、`/strategy-rules/close`、`/approvals/create`、`/approvals/decide` 路由 |
| `src/adwatch/dashboard/app.py` | 修改 | 注册 `/approvals` 到 `module_renderers`；注入 `StrategyRuleService` 和 `ApprovalService` 到 `DashboardRouter` |
| `src/adwatch/audit/service.py` | **新建** | 审计事件查询、时间线生成 |
| `src/adwatch/audit/__init__.py` | **新建** | 包初始化 |
| `tests/dashboard/test_routes.py` | 新增 | 策略规则路由测试、审批路由测试 |
| `tests/dashboard/test_views.py` | 新增 | 审批页面渲染测试 |
| `tests/approval/test_service.py` | 新增 | `list_approvals()` 查询测试 |

### 多智能体分工

| 任务 | 智能体/Skill | 输出 |
|:----:|-------------|------|
| **7.1** 策略规则 Web 维护 | `python-engineer` + `ui-designer` | `render_strategy_rules_page()` 新增创建版本表单；`routes.py` 新增 `/strategy-rules/create`、`/strategy-rules/close` POST 路由；`app.py` 注册 |
| **7.2** `/approvals` 页面 | `python-engineer` + `ui-designer` | 新增 `render_approvals_page()`：审批列表、创建审批表单、决策表单、执行审计面板、熔断时间线 |
| **7.3** 审计时间线 | `python-engineer` | 新建 `audit/service.py`：审计事件查询、按对象 ID 分组时间线；`render_approvals_page()` 集成 |
| **7.4** Shadow 模式闭环 | `python-engineer` | 修改 `routes.py`：Shadow 模式路由（评估不写入 `recommendations` 表）；`render_approvals_page()` 展示 Shadow 结果 |

### 关键设计决策

1. **策略规则表单**：用单个表单覆盖"新增版本"（参数 JSON 输入 + 生效日期 + 创建人），不拆分多个小表单。`DashboardRouter` 新增 `strategy_rule` 依赖注入。
2. **审批页面**：从 `approvals` 表 JOIN `recommendations` 表查询，支持按状态（pending/approved/rejected/expired）筛选；展示决策时间线（创建→审批→执行→回滚）。
3. **审计时间线**：从 `audit_events` 表（已有 migration 2 创建）查询，按对象 ID 分组，渲染为时间线卡片。
4. **Shadow 模式**：路由 `/strategy-rules/shadow` 执行 `recommend()` 但不写入 `recommendations` 表，结果存入 `shadow_results` 临时表或日志。
5. **页面路由注册**：`app.py` 中 `/approvals` 从 `module_pages` 字典移入 `module_renderers` 字典。
6. **CSRF 保护**：所有表单使用现有 `csrf_token` 机制，复用 `DashboardRouter.post()` 的 CSRF 校验。

### 验证要点

- [ ] 策略规则新增版本表单提交后正确跳转到 `/strategy-rules`，新版本出现在列表中
- [ ] 关闭版本生效期后，该版本标记为过期
- [ ] `/approvals` 页面展示完整列表，含过期状态标记
- [ ] 审计时间线按对象正确分组渲染
- [ ] Shadow 模式运行后不产生实际推荐记录
- [ ] 全量 pytest 通过

---

## 批次三：P2-C — 周报、月报和每周调度

### 当前状态

| 模块 | 状态 | 说明 |
|------|------|------|
| `render_weekly_markdown()` | 日报拼接 | 只是把 7 份日报拼在一起，无周维度聚合、环比、TOP/BOTTOM |
| `render_monthly_markdown()` | 仅广告汇总 | 只有 spend/gmv/orders，无经营利润、成本、费用 |
| `ReportReadModel` | 只有 `daily()` | 无 `weekly()`、`monthly()` 查询方法 |
| `deliver_report()` | 只支持日报 | 标题模板固定为日报格式 |
| 周报调度 | 未实现 | 无独立调度器 |
| 月报调度 | 未实现 | 无独立调度器 |
| macOS LaunchAgent | 只有每日 | `com.adwatch.daily`，无每周/每月 agent |
| `RunStateStore` | 只有每日状态 | 无周报/月报运行状态记录 |

### 涉及文件

| 文件 | 修改类型 | 说明 |
|------|---------|------|
| `src/adwatch/reporting/read_model.py` | 修改 | 新增 `weekly()`、`monthly()` 查询方法 |
| `src/adwatch/reporting/markdown.py` | 修改 | 重写 `render_weekly_markdown()`（周聚合、环比）；重写 `render_monthly_markdown()`（经营利润、成本、费用） |
| `src/adwatch/reporting/delivery.py` | 修改 | `deliver_report()` 支持周报/月报标题模板和文件名模式 |
| `src/adwatch/operations/scheduler.py` | 修改 | 新增 `LAUNCH_AGENT_LABEL_WEEKLY`；`render_launchd_plist()` 支持 `schedule_type` 参数 |
| `src/adwatch/operations/scheduled_daily.py` | 修改 | 新增 `run_scheduled_weekly()` 函数 |
| `src/adwatch/operations/run_state.py` | 修改 | 新增 `last_successful_weekly_date()`、`record_weekly_result()`、`weekly_lock_path` |
| `src/adwatch/config.py` | 修改 | 新增 `weekly_run_hour`、`weekly_run_minute` 配置项 |
| `src/adwatch/cli.py` | 修改 | 新增 `adwatch run scheduled-weekly` 命令 |
| `tests/reporting/test_markdown.py` | 新增 | 周报聚合测试、月报利润测试 |
| `tests/reporting/test_read_model.py` | 新增 | `weekly()`、`monthly()` 查询测试 |
| `tests/scheduler/test_scheduler.py` | 新增 | 每周一调度测试 |

### 多智能体分工

| 任务 | 智能体/Skill | 输出 |
|:----:|-------------|------|
| **8.1** 周报聚合与环比 | `python-engineer` | `ReportReadModel.weekly()` 聚合 7 日数据；重写 `render_weekly_markdown()`：周汇总、每日趋势、环比、TOP/BOTTOM SKU、异常分布 |
| **8.2** 月报经营利润 | `python-engineer` | `ReportReadModel.monthly()` 聚合月数据；重写 `render_monthly_markdown()`：月收入、成本、费用、净利润、现金变化、每周趋势 |
| **8.3** 幂等性与完整度 | `python-engineer` | 周期报告幂等（幂等键）、缺日提示、来源完整度指标；`deliver_report()` 扩展支持周报/月报 |
| **8.4** 每周一调度 | `python-engineer` | 新增 `run_scheduled_weekly()` 函数；`scheduler.py` 注册每周一 LaunchAgent；锁隔离；`RunStateStore` 扩展周报状态 |

### 关键设计决策

1. **周报数据源**：从 `ReportReadModel.weekly()` 获取 7 天聚合数据，在 Python 层做聚合（避免 SQL 层过于复杂）。数据来自 `daily_ad_metrics`、`profit_results`、`alerts`、`recommendations`、`collection_runs` 表。
2. **月报数据源**：`ReportReadModel.monthly()` 从 `profit_results` 表获取月利润汇总，结合 `daily_ad_metrics` 获取广告指标，从 `ledger` 获取费用。
3. **环比计算**：当前周 vs 上周（`week_end - 14` 至 `week_end - 7`）；当前月 vs 上月。
4. **调度锁隔离**：周报使用独立的 `scheduled-weekly.lock` 文件，不与日报锁冲突。
5. **月报集成**：月报无需独立 LaunchAgent，在周报调度中判断"本周是否是月最后一周"来额外生成月报。
6. **报告文件命名**：`weekly-{week_end}.md`、`monthly-{year}-{month}.md`，与日报同目录。
7. **通知**：周报/月报通过 `deliver_report()` 发送到飞书 webhook，使用不同标题模板区分。

### 数据库查询设计

```sql
-- 周报聚合查询（示例）
SELECT 
    platform, store,
    SUM(spend) AS total_spend,
    SUM(attributed_gmv) AS total_gmv,
    SUM(orders) AS total_orders,
    AVG(roas) AS avg_roas
FROM daily_ad_metrics
WHERE data_date >= ? AND data_date <= ?
GROUP BY platform, store;

-- 周报利润查询
SELECT
    platform, store,
    SUM(net_profit_cny) AS total_net_profit
FROM profit_results
WHERE data_date >= ? AND data_date <= ?
GROUP BY platform, store;
```

### 验证要点

- [ ] 周报正确聚合 7 日数据，环比百分比正确
- [ ] 月报包含收入、成本、费用、净利润、现金变化
- [ ] 缺日提示正确（如某天无数据时标记"缺日"）
- [ ] 每周一调度自动触发，运行锁防止重复
- [ ] 周报/月报文件名正确，与日报不冲突
- [ ] 全量 pytest 通过

---

## 批次四：P2-D — 实验体系与备用采集

### 当前状态

| 模块 | 状态 | 说明 |
|------|------|------|
| `experiments/` 目录 | ✅ 已创建 | 含 models、guardrails、registry、service 完整模块 |
| 采集器 | ✅ 已实现 | `ZiniaoCollector` + `MockCollector` + `FallbackCollector` 三类采集器 |
| 备用采集切换 | ✅ 已实现 | 主采集连续失败 3 次切换备用，备用成功 2 次恢复主采集 |
| `CircuitInputs` + `evaluate_circuit()` | 已实现 | 用于全局熔断判断 |
| `ZiniaoCliClient.navigate_and_exec()` | 已实现 | 支持页面导航和脚本执行 |
| 实验数据模型 | ✅ 已实现 | 实验分组、变体、窗口、护栏实体完整 |
| 实验状态机 | ✅ 已实现 | draft→running→paused→completed/reverted/cancelled 完整生命周期 |
| 操作时间线 | ✅ 已实现 | 实验操作审计记录写入 `audit_events` 表 |

### 涉及文件

| 文件 | 修改类型 | 说明 |
|------|---------|------|
| `src/adwatch/experiments/__init__.py` | **新建** | 包初始化 |
| `src/adwatch/experiments/models.py` | **新建** | 实验分组、窗口、护栏、结果状态机 |
| `src/adwatch/experiments/registry.py` | **新建** | 实验注册中心 |
| `src/adwatch/experiments/service.py` | **新建** | 实验管理服务（创建、分配、评估） |
| `src/adwatch/experiments/guardrails.py` | **新建** | 护栏评估逻辑 |
| `src/adwatch/collectors/registry.py` | **新建** | 采集器注册中心 |
| `src/adwatch/collectors/fallback.py` | **新建** | 备用采集器（通过 Ziniao CLI 通用浏览器能力执行） |
| `src/adwatch/operations/daily_run.py` | 修改 | `collect()` 集成 `CollectorRegistry` 的备用切换逻辑 |
| `src/adwatch/storage/migrations.py` | 修改 | 新增迁移：`experiments`、`experiment_variants`、`experiment_windows`、`experiment_guardrail_breaches`、`collector_health` 表 |
| `src/adwatch/dashboard/views.py` | 修改 | 新增实验管理页面（可选） |
| `src/adwatch/dashboard/routes.py` | 修改 | 新增实验管理路由（可选） |
| `tests/experiments/` | **新建** | 实验全量测试 |
| `tests/collectors/test_fallback.py` | **新建** | 备用采集测试 |

### 多智能体分工

| 任务 | 智能体/Skill | 输出 |
|:----:|-------------|------|
| **9.1** 实验分组与状态机 | `python-engineer` | 新建 `experiments/` 模块：`Experiment`、`ExperimentVariant`、`ExperimentWindow`、`ExperimentGuardrailBreach` 实体；实验状态机（draft→running→paused→completed/reverted）；DB 迁移 |
| **9.2** 商品测试/A/B/操作时间线 | `python-engineer` | 新建 `experiments/service.py`：创建实验、分配变体（按 store_id 哈希分桶）、记录操作时间线到 `audit_events` 表；`experiments/guardrails.py`：护栏评估逻辑 |
| **9.3** 备用采集切换 | `python-engineer` | 新建 `collectors/registry.py`：采集器注册中心；`collectors/fallback.py`：备用采集器；`daily_run.py` 集成切换逻辑（连续失败 3 次切换，成功 2 次恢复） |

### 关键设计决策

1. **实验分组**：按 store_id 哈希分桶，支持 50/50 或 80/20 比例。同一 store_id 始终分配到同一组。
2. **护栏条件**：独立于全局熔断器，配置在 `guardrail_config_json` 中。关键指标：`max_daily_spend`、`max_roas_drop_pct`、`max_daily_alert_count`、`min_data_days`。
3. **备用采集**：主采集连续失败 3 次后自动切换到备用采集器；备用采集成功 2 次后自动切回主采集。切换事件写入 `audit_events` 表。
4. **备用采集器实现**：不直接集成 browser-use SDK，而是通过 Ziniao CLI 的通用浏览器能力执行，减少依赖。
5. **操作时间线**：记录在 `audit_events` 表中（复用已有表），新增 `event_type` 值如 `experiment.assigned`、`experiment.started`、`experiment.completed`、`collector.failover`。
6. **采集器健康表**：`collector_health` 表记录每个采集器的健康状态、连续失败/成功次数、故障切换状态。

### 数据库迁移设计

```sql
-- 迁移 N+1: experiments 相关表
CREATE TABLE experiments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL,
    store TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK(status IN ('draft','running','paused','completed','reverted','cancelled')),
    starts_on TEXT NOT NULL,
    ends_on TEXT NOT NULL,
    guardrail_config_json TEXT NOT NULL DEFAULT '{}',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE experiment_variants (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(id),
    variant_type TEXT NOT NULL CHECK(variant_type IN ('control','treatment')),
    name TEXT NOT NULL,
    strategy_params_json TEXT NOT NULL DEFAULT '{}',
    traffic_percent REAL NOT NULL DEFAULT 50.0
        CHECK(traffic_percent >= 0 AND traffic_percent <= 100),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active','paused','removed')),
    created_at TEXT NOT NULL
);

CREATE TABLE experiment_windows (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(id),
    data_date TEXT NOT NULL,
    variant_id TEXT NOT NULL REFERENCES experiment_variants(id),
    metrics_json TEXT NOT NULL DEFAULT '{}',
    guardrail_breaches_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE experiment_guardrail_breaches (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(id),
    window_id TEXT REFERENCES experiment_windows(id),
    metric_name TEXT NOT NULL,
    threshold REAL NOT NULL,
    actual_value REAL NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('warning','critical')),
    occurred_at TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0
);

-- 迁移 N+2: collector_health 表
CREATE TABLE collector_health (
    collector_name TEXT NOT NULL,
    platform TEXT NOT NULL,
    healthy INTEGER NOT NULL DEFAULT 1,
    last_checked_at TEXT NOT NULL,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    consecutive_successes INTEGER NOT NULL DEFAULT 0,
    failover_active INTEGER NOT NULL DEFAULT 0,
    failover_started_at TEXT,
    last_error TEXT,
    PRIMARY KEY(collector_name, platform)
);
```

### 验证要点

- [x] 实验分组正确分配且稳定（同一 store_id 始终分配到同一组）
- [x] 实验状态机正确转换（draft→running→paused→completed/reverted）
- [x] 护栏条件正确阻止不符合条件的实验（超 spend、ROAS 骤降等）
- [x] 主采集连续失败 3 次后正确切换到备用采集
- [x] 备用采集成功 2 次后自动切回主采集
- [x] 切换事件正确记录到 `audit_events` 表
- [x] 备用采集结果标记为 `source="fallback-{platform}"`
- [x] 全量 pytest 541 passed

---

## 执行顺序与依赖

```
Step 1: P2-A (6.1→6.2→6.3→6.4)    ✅ 已完成
Step 2: P2-B (7.1→7.2→7.3→7.4)    ← 依赖 P2-A 数据契约冻结
Step 3: P2-C (8.1→8.2→8.3→8.4)    ← 独立，可与 P2-B 并行
Step 4: P2-D (9.1→9.2→9.3)        ← 增强项，与 P2-C 在 daily_run.py 有冲突，建议串行
```

**建议执行策略**：
1. 先做 P2-B（7.1→7.4）和 P2-C（8.1→8.4）并行开发
2. 再串行 P2-D（9.1→9.3）

---

## 风险管理

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 审批流程复杂 | P2-B 开发周期长 | 先做最简可用（表单+列表），Shadow 模式延后 |
| 周报月报数据量大 | P2-C 查询慢 | 加索引；SQL 聚合而非 Python 聚合 |
| 备用采集未经真实测试 | P2-D 可靠性存疑 | Mock 模式模拟故障；切换日志记录完整 |
| 实验护栏与全局熔断混淆 | P2-D 逻辑错误 | 实验护栏独立于全局熔断器，互不干扰 |
| P2-C 与 P2-D 在 daily_run.py 冲突 | 合并冲突 | 串行执行，先 P2-C 后 P2-D |

---

## 验证总清单

- [ ] P2-A：明细采集正确、翻页完整、对账通过 ✅
- [ ] P2-B：策略规则 Web 维护、审批页面、审计时间线、Shadow 闭环
- [ ] P2-C：周报聚合环比、月报经营利润、周一调度自动触发
- [x] P2-D：实验分组稳定、护栏生效、备用采集切换正确（541 passed）
- [x] 全量 pytest 保持 541 通过
- [x] Ruff 0、compileall 通过、git diff --check 通过
- [ ] 文档更新（task_plan.md、progress.md、findings.md）

---

## 执行计划（分周）

### 第一周：P2-B 策略规则与审批

| 天数 | 任务 | 验证 |
|------|------|------|
| 第1-2天 | 7.1 策略规则 Web 维护 | 表单提交后正确跳转，新版本出现在列表 |
| 第3-4天 | 7.2 `/approvals` 页面 | 审批列表展示完整，状态筛选正确 |
| 第5天 | 7.3 审计时间线 | 时间线按对象正确分组 |
| 第6天 | 7.4 Shadow 模式闭环 | Shadow 模式不产生实际推荐 |
| 第7天 | 全量测试 + 代码审查 | 430+ passed, Ruff 0 |

### 并行：P2-C 周报月报调度（与 P2-B 并行）

| 天数 | 任务 | 验证 |
|------|------|------|
| 第1-2天 | 8.1 周报聚合与环比 | 周报正确聚合 7 日数据，环比正确 |
| 第3-4天 | 8.2 月报经营利润 | 月报含收入、成本、费用、利润 |
| 第5天 | 8.3 幂等性与完整度 | 幂等键生效，缺日提示正确 |
| 第6天 | 8.4 每周一调度 | 调度自动触发，运行锁防止重复 |
| 第7天 | 全量测试 + 代码审查 | 430+ passed, Ruff 0 |

### 第二周：P2-D 实验体系与备用采集

| 天数 | 任务 | 验证 |
|------|------|------|
| 第1-2天 | 9.1 实验分组与状态机 | 实体完整，状态机正确转换 |
| 第3-4天 | 9.2 商品测试/A/B/操作时间线 | 实验创建、分配变体、护栏评估 |
| 第5-6天 | 9.3 备用采集切换 | 切换条件正确，切换事件记录完整 |
| 第7天 | 全量测试 + 代码审查 | 430+ passed, Ruff 0 |

---

## 附录：多智能体 Skills 调用清单

| 阶段 | 前置 Skill | 主智能体 | 后置 Skill |
|------|-----------|---------|-----------|
| 7.1 策略规则 Web 维护 | `通用模块调用指南`（了解 DashboardRouter 模式） | `python-engineer` + `ui-designer` | `TRAE-code-review` |
| 7.2 `/approvals` 页面 | `通用模块调用指南` | `python-engineer` + `ui-designer` | `TRAE-code-review` |
| 7.3 审计时间线 | — | `python-engineer` | `TRAE-code-review` |
| 7.4 Shadow 模式 | — | `python-engineer` | `TRAE-code-review` |
| 8.1-8.2 周报月报 | `通用模块调用指南`（了解 ReportReadModel） | `python-engineer` | `TRAE-code-review` |
| 8.3-8.4 调度 | `通用模块调用指南`（了解 Scheduler） | `python-engineer` | `TRAE-code-review` |
| 9.1-9.2 实验体系 | — | `python-engineer` | `TRAE-code-review` |
| 9.3 备用采集 | `通用模块调用指南`（了解 Collector 模式） | `python-engineer` | `TRAE-code-review` |