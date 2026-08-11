# AdWatch P1-B Income、结算与平台费用实施计划

> **执行技能：** `executing-plans`、`test-driven-development`、`pdf`、
> `requesting-code-review`、`verification-before-completion`。

**目标：** 从 Shopee Income PDF 建立可审计的结算周期、实际平台费率、
结算摘要和广告充值事实；每天 09:00 自动扫描新文件，并让利润结果和日报记录
实际采用的费率版本。TikTok 没有真实样本，继续返回 `pending_external`，不得猜测。

**当前基线：** 迁移版本为 21；日任务顺序为汇率、Bridge、Shopee 经营事实、
广告采集、分析、报告。现有两份 Shopee 周报内容 SHA-256 相同，均为两页 A4，
稳定包含 2026-07-20 至 2026-07-26 的 11 个汇总字段。主体使用迁移 22，
审查修复迁移 23 为归一化事实补充 statement/version/current 血缘，
不沿用旧计划中的迁移 18、10:00 调度或旧目录相对命令。

## 1. 业务与安全边界

- 只扫描 `weekly_report_*.pdf`，不移动、重命名、删除或改写原文件。
- PDF 原文、姓名、地址、银行信息和完整本机路径不得进入 CLI、故障日志、日报
  或测试夹具；仅保存文件名、SHA-256、平台/标准店铺、周期和业务汇总字段。
- 综合销售费率只含 Commission、Service、Transaction、AMS/Affiliate 四类费用。
- Voucher、买家运费、物流扣费、平台运费补贴、广告充值和 Adjustment 分开保存。
- Ads Credit Top-Up 是结算中资助广告账户的事实，不等于广告消耗。
- Total Payout 是结算事实，不等于收入确认；不得直接冒充订单收入或重复扣费。
- 费用未知保持 `NULL/pending_data`，不得猜测为 0；报表明确标注费率来源。
- 同 SHA 重复扫描为 skipped；同周期不同 SHA 为 revision，保留全部历史版本。
- 每日扫描失败记录 `income_scan` 独立故障并使日任务 partial，但不阻断后续采集、
  分析和报告。

## 2. 目标数据结构（迁移 22；审查修复迁移 23）

### `platform_fee_statements`

- 不可变版本主键 `id`。
- 唯一键：`source_sha256`；周期版本键：
  `(platform, store, period_start, period_end, version)`。
- `is_current` 标记同周期当前版本；修订时旧版本保留并置为 0。
- 保存标准店铺、原始店铺别名、周期、币种、各汇总金额、实际费率、
  `source_name`、SHA 和导入时间。

### `income_import_events`

- 每个已处理 SHA 一行，状态为 `imported|revised|quarantined|failed`。
- 只保存结构化 `reason_code`，不保存 PDF 原文或异常正文。
- quarantine/failed 同样阻止每天重复制造相同故障；人工 `import` 可显式复核。

### 归一化经营事实

- `platform_settlement_summaries`：周期商品总额与实际 payout，明确
  `recognition_status='settlement_only'`。
- `platform_fee_entries`：每个 SHA/fee_type 一行，四类销售费及 Voucher/物流/
  补贴/Adjustment 分开。
- `platform_ad_funding_observations`：Ads Credit Top-Up，来源为
  `settlement_deduction`，与 `ad_spend_entries` 分离。
- 迁移 23 为三类归一化事实增加 `statement_id`、`statement_version` 和
  `is_current`；同周期修订保留历史，只有当前版本参与后续汇总。

### `profit_results` 费率血缘列

新增 `fee_effective_rate`、`fee_period_start`、`fee_period_end`、
`fee_selection`、`fee_source_name` 和 `fee_source_sha256`。选择优先级：

1. 周期覆盖数据日：`covering_period`；
2. 数据日前最近有效周期：`carried_forward`；
3. 原 `product_costs.commission_rate`：`legacy_cost_rate`；
4. 都没有：`pending_data`，不生成已验证利润。

## 3. 文件范围

**Create**

- `src/adwatch/income/__init__.py`
- `src/adwatch/income/models.py`
- `src/adwatch/income/repository.py`
- `src/adwatch/income/shopee_pdf.py`
- `src/adwatch/income/service.py`
- `tests/income/test_repository.py`
- `tests/income/test_shopee_pdf.py`
- `tests/income/test_service.py`
- `tests/test_income_cli.py`
- `tests/storage/test_income_migration.py`

**Modify**

- `pyproject.toml`
- `.env.example`
- `src/adwatch/config.py`
- `src/adwatch/storage/migrations.py`
- `src/adwatch/storage/analytics.py`
- `src/adwatch/integrations/commerce.py`
- `src/adwatch/analytics/service.py`
- `src/adwatch/operations/daily_run.py`
- `src/adwatch/reporting/read_model.py`
- `src/adwatch/reporting/markdown.py`
- `src/adwatch/cli.py`
- `tests/test_config.py`
- `tests/integrations/test_commerce.py`
- `tests/analytics/test_service.py`
- `tests/operations/test_daily_run.py`
- `tests/reporting/test_read_model.py`
- `tests/reporting/test_markdown.py`
- `README.md`
- `docs/operations/data-foundation.md`
- `findings.md`
- `task_plan.md`
- `progress.md`

## 4. TDD 执行任务

### B1：冻结脱敏 Income 字段契约

- [x] 用字符串夹具覆盖周期、店铺别名和 11 个稳定汇总字段，不包含姓名、地址、
  银行账号或真实路径。
- [x] 写 PDF 安全字段测试，并确认解析模块不存在时 RED。
- [x] 固定 payout 对平公式和 0.01 THB 容差。

Run：`.venv/bin/python -m pytest tests/income/test_shopee_pdf.py -q`

### B2：迁移 22、不可变版本与选择仓储

- [x] 先写新表、约束、利润血缘列、旧库升级和迁移版本测试并确认 RED。
- [x] 实现 `FeeStatement`、`ResolvedFeeRate`、结构化导入结果与所有金额校验。
- [x] 实现同 SHA skipped、同周期 revision 保留旧版本、覆盖期/沿用/跨店铺隔离。
- [x] 依赖已经修复的原子迁移机制，增加迁移 22 故障回滚回归。

Run：`.venv/bin/python -m pytest tests/storage/test_income_migration.py tests/income/test_repository.py -q`

### B3：Shopee PDF 解析器

- [x] 添加 `pypdf>=6,<7` 运行依赖；测试只使用脱敏文本，不提交真实 PDF。
- [x] 实现 `extract_pdf_text()`、字段别名、Unicode 负号/币种/千分位解析。
- [x] 四类销售费计算实际费率；其他字段保持独立正成本或有符号 Adjustment。
- [x] 缺字段、商品额非正、周期非法、费率超界或 payout 不平全部抛
  `IncomeParseError`，不产生半条事实。

Run：`.venv/bin/python -m pytest tests/income/test_shopee_pdf.py -q`

### B4：导入服务、隔离与归一化经营事实

- [x] 写已知别名导入、未知店铺隔离、重复扫描、周期修订和原子失败测试。
- [x] `IncomeImportService` 先哈希、解析、通过 `store_aliases` 唯一映射标准店铺，
  再在一个事务内保存 statement、event、settlement、fee 和 funding 事实。
- [x] `CommerceRepository` 为三类周期事实提供按 external key 幂等的 upsert；
  禁止写入 `daily_ad_metrics` 或把 payout 写成订单收入。

Run：`.venv/bin/python -m pytest tests/income/test_service.py tests/integrations/test_commerce.py -q`

### B5：配置和 `income import|scan|list` CLI

- [x] 新增 `ADWATCH_INCOME_SCAN_ROOT`，默认 macOS 紫鸟数据根目录且支持服务器覆盖。
- [x] CLI import 只支持 Shopee；TikTok 返回 `pending_external`。
- [x] scan 输出 discovered/imported/revised/skipped/quarantined/failed 固定计数。
- [x] list 只输出平台、标准店铺、周期、费率、选择状态、文件名和短 SHA，
  不输出原文/完整路径/个人字段。

Run：`.venv/bin/python -m pytest tests/test_income_cli.py tests/test_config.py -q`

### B6：接入每日 09:00 流程

- [x] 在汇率后、Bridge 前调用同一个 `IncomeImportService.scan()`，mock 模式跳过。
- [x] 扫描成功写摘要消息；quarantine/failed 或异常记录 `income_scan` incident，
  日任务为 partial，但继续 Bridge、订单、广告、分析和报告。
- [x] 更新 `DailyRunDependencies` 测试桩和完整顺序测试。

Run：`.venv/bin/python -m pytest tests/operations/test_daily_run.py tests/test_daily_run_cli.py -q`

### B7：利润选择实际费率并保存血缘

- [x] 先写覆盖期、沿用、legacy fallback、无费率 pending、跨平台/店铺隔离测试。
- [x] `AnalyticsRepository.load_analysis_rows()` 只 join 当前 statement 版本；
  返回 resolved rate 和选择血缘。
- [x] `AnalysisService` 用 resolved rate 计算，并把使用的版本写入
  `profit_results`；重跑确定性覆盖相同血缘。

Run：`.venv/bin/python -m pytest tests/analytics tests/storage -q`

### B8：日报展示实际费率来源

- [x] `DailySnapshot` 增加实际使用的费率摘要；同平台多店铺分别显示。
- [x] 日报仅显示 `source_name`，展示周期、费率与“覆盖/沿用/历史成本”状态；
  缺失显示待补数据，不显示 0%。
- [x] 飞书 Markdown 与本地日报共用同一读取模型。

Run：`.venv/bin/python -m pytest tests/reporting tests/dashboard -q`

### B9：真实 PDF、幂等、文档和质量门禁

- [x] 先创建 production SQLite 完整性备份，再对配置扫描根目录执行 import/scan。
- [x] 验证两份同 SHA PDF 只产生一个 statement 版本；周期对平，四类销售费合计
  1061 THB、实际费率 0.237201、payout 3364 THB，广告充值 13 THB 独立。
- [x] 对 2026-07-26 和 2026-07-27 重跑分析：前者 covering，后者 carried；
  检查利润结果和日报来源一致，且不发送飞书。
- [x] 扫描日志、日报和数据库非业务列做 PII/会话检查；原 PDF 保持相同 SHA。
- [x] 更新 README、数据底座、findings、任务状态，运行代码审查并修复所有
  Critical/Important。
- [x] 运行全量 pytest、Ruff、compileall 和 `git diff --check`。

## 5. 完成门槛

- 真实 PDF 可对平并幂等导入；同周期修订保留历史；未知店铺可解释隔离。
- 三类归一化事实可查询，充值/消耗、结算/收入确认不会混淆。
- 每日扫描故障独立可见且不阻断其他数据链，TikTok 继续禁采。
- 利润结果和日报可追溯到实际费率周期、选择方式、文件名和 SHA。
- 所有自动化与真实只读验收通过，无 PDF PII、完整路径或会话信息泄漏。
- 不自动 commit、push 或部署；继续在当前唯一工作区内实施下一批。
