# 蓝图对标审计发现

用于记录蓝图要求、仓库证据和经营参数设计结论。

## 第一轮证据

- 仓库约 3,240 行生产代码、1,570 行测试代码，当前 `main` 为 `fe6b3f6`。
- SQLite 有 2026-07-21 至 2026-07-23 三天 Shopee 真实汇总数据；TikTok 当前无 Campaign 数据。
- 已有采集、清洗、SQLite、利润、异常、策略、审批、执行、日报、飞书、看板等模块。
- launchd 已安装且曾成功发送日报，但最新一次退出码为 1：紫鸟浏览器 Bridge 未启动。因此“无人值守稳定运行”尚未通过。
- 蓝图 P0 的三天人工对账准确率 >=99% 已有报告证据；但只覆盖 Shopee 有数据场景，TikTok 无 Campaign。
- 蓝图 P1/P2 多数已有代码骨架；是否完成必须按真实数据覆盖、交互回调和广告写入验收降级判断。

## 审计错误

- 首次查询 `alerts.alert_type` 失败：实际字段名不同。下一轮先读取 schema 再查询，不重复猜列名。

## 最终结论

- 蓝图 34 个路线 checkbox：完成 15、部分 12、未完成 7；折算实现覆盖约 62%，真实生产闭环成熟度约 40%。
- 当前运行库 `profit_results`、`recommendations`、`approvals`、`execution_audits` 均为 0；核心原因是经营数据未输入，而不是相应表或模块完全不存在。
- 当前 CSV 设计混合了自动事实、一次性配置、每日人工数据和库存数据，应该拆分。
- 推荐人工日输入缩减为 `data_date,total_product_cost,refund_amount`；订单、GMV、广告花费沿用自动采集。
- 若仅使用“日期、订单、成本”，只有当成本定义为除广告费外全部成本时，才足以估算净利润；若只是商品成本，则不足。

## 用户确认的执行边界

- P2 主线使用紫鸟 CLI 操作页面，不以官方广告 API 为前置。
- 页面修改预算、目标 ROAS、暂停/恢复并保存，均属于真实广告写操作。
- 开发期间使用 Shadow 模式验证定位、读值、计算、审批、截图和审计，但不点击最终保存。
- Live Backend 可以开发完成，但默认关闭；真实数据和外部条件最后统一列为上线待办。
- P3 官方 API 是可选增强，权限缺失不阻塞紫鸟 CLI 主线开发。
- 2026-07-30 起，唯一生产代码源明确为 `/Users/yl/vscode/Cross_Border_Electronic_Commerce`。
- 必须注销所有指向 `/Users/yl/Documents/跨境电商/广告盯盘自动化` 的旧定时任务，并从唯一代码源注册北京时间每天 09:00 的 macOS 定时任务。
- 生产数据目录统一为唯一代码源下的 `var/`；当前代码需重新补跑 2026-07-29。
- 2026-07-29 本次补跑不发送飞书，避免与旧目录 20:49 生成的日报发生重复通知；后续 09:00 定时运行及故障通知仍使用现有飞书 Webhook。
- 用户批准“单一受管 LaunchAgent + 到期判断包装器”方案：提供 `schedule install/status/uninstall/repair`，使用 `RunAtLoad` 做登录后漏跑判断，正常计划固定 09:00。
- 不采用双 LaunchAgent 守护方案或系统 LaunchDaemon/独立登录应用；前者仍依赖同一登录域扫描，后者涉及管理员权限且不利于访问紫鸟 GUI Bridge。
- 用户批准运行数据流与故障闭环：运行锁、登录后到期判断、Bridge 三次探测、独立 JSONL 故障日志、飞书故障卡片去重、`--no-notify` 和时区检查。
- 新增顺序要求：每天北京时间 09:00 先采集执行当天汇率；当天没有时使用昨天汇率，再进行广告采集和分析。
- 现有每日流程已经在广告采集前调用 `ensure_exchange_rate()`，但传入的是日报数据日期（通常昨天），不是北京时间执行日期。
- `EcbExchangeRateSource.fetch_range()` 会把最近公布值填充到请求日期；`ensure_exchange_rate()` 因而可能把前一交易日汇率标记为请求日 `fresh`，无法准确区分当天发布值与回退值，需要保留真实来源日期。
- 利润分析严格按 `exchange_rates.rate_date = daily_ad_metrics.data_date` 关联汇率。若“当天”指任务执行日，而日报分析的是昨天数据，则必须明确把选定的执行日汇率快照应用到日报数据日期，同时保留真实汇率来源日期；否则采到了今天汇率，昨天分析仍会缺汇率。
- 用户确认日期口径：例如 2026-07-30 09:00 先取 2026-07-30 汇率，无则使用 2026-07-29 汇率，并把最终选定值用于 2026-07-29 广告利润分析。

## 2026-07-28 上线恢复检查

- 正式上线检查已完成紫鸟 Bridge、Shopee Campaign 验证和经营成本输入。
- 当前剩余 12 项，其中平台 API OAuth 是可选增强。
- `/Users/yl/Library/LaunchAgents/com.adwatch.daily.plist` 格式正确，目标为每天 08:00 执行 `adwatch run daily --mode ziniao`。
- `launchctl` 当前未注册 `com.adwatch.daily`，因此连续三天对账前必须先恢复该服务。
- 已导入订单成本日期为 2026-07-08 至 2026-07-17；现有广告数据主要为 2026-07-23 至 2026-07-26，尚无可用于利润对账的日期交集。
- 2026-07-28 08:56 手动触发 LaunchAgent 成功：`daily_run=ok metrics=1 delivery=sent`，证明 launchd 环境、紫鸟 Bridge、Shopee 采集和飞书发送链路均正常。
- 本次 2026-07-27 Shopee 数据为消耗 400 THB、GMV 1066 THB、5 单、ROAS 2.66；TikTok 无数据。
- 本次利润仍缺 `product_cost, commission_rate, inventory_units, expected_daily_units, rate_to_cny, start_date, target_roas`。
- Shopee 商品页确认泡脚包 Item ID `57861884313`；三个正式 Seller SKU 为
  `Foot Soak Bag-one bag`、`Foot Soak Bag-two bags`、
  `Foot Soak Bag-three bags`，Model ID 分别为 `311033956020`、
  `311033956021`、`311033956022`，页面库存分别为 31、45、48。
- 订单状态、物流状态和退款状态均可由紫鸟 CLI 页面读取，不应成为人工成本
  模板的维护字段。

## 2026-07-30 LaunchAgent 重启故障

- 2026-07-29 09:00 曾正常执行并发送 2026-07-28 日报，说明当时采集与飞书链路可用。
- Mac 于 2026-07-29 10:19 重启；2026-07-30 09:07 检查时，`launchctl` 找不到 `com.adwatch.daily`。
- plist 仍存在且格式正确，当前用户确认计划时间为 09:00；未生成 `daily-2026-07-29.md`，故障发生在程序启动之前。
- 根因边界：现有安装方式只加载当前登录会话，未提供新登录会话中的可靠自恢复。
- 用户要求的修复范围：立即重载并补跑、登录后自恢复、运行前检查紫鸟 Bridge、独立故障日志、飞书故障通知、重启/重新登录持久化验收。
- 当前工作树已有大量用户改动与文档目录迁移；本次只触碰与 Adwatch 运维恢复直接相关的文件。
- 当前 plist 已重新出现在 `gui/501`，状态为 `not running`、`runs = 0`，下一次触发为 09:00；这只证明本次会话重新加载成功，尚不能证明重启持久化。
- 已安装 plist 的执行目录是 `/Users/yl/Documents/跨境电商/广告盯盘自动化`，而当前维护仓库是 `/Users/yl/vscode/Cross_Border_Electronic_Commerce`；需确认两者关系，避免修改未被生产任务使用的副本。
- plist 当前只配置 `StartCalendarInterval`，`RunAtLoad=false`，没有登录自恢复监督任务；标准输出与错误输出共用普通 launchd 日志，缺少独立故障日志。
- 现有 `deliver_report()` 支持日报卡片发送及本地回退，但没有独立的运维故障通知接口。
- 历史实施计划曾要求“launchd 前置检查失败时保存本地状态并发送飞书故障通知”，现有代码未落实该要求。
- Apple 官方文档说明：用户登录时，per-user `launchd` 会从用户的 `~/Library/LaunchAgents` 读取并注册 LaunchAgent。因此仅再增加一个同目录 plist 不能形成独立恢复通道；安装流程必须保证文件、启用状态、运行路径和当前登录域一致，并以登录后探针验证注册结果。
- `launchctl print-disabled gui/501` 显示 `com.adwatch.daily` 当前为 enabled，plist 所有者和权限也正常；本次故障不是当前的 disabled 覆盖或明显权限错误。
- 当前源代码 `render_launchd_plist()` 仍硬编码 08:00；工作树中的 `Settings.daily_run_time=09:00` 尚未接入 plist 渲染。已安装 plist 的 09:00 与代码无法一致重建。
- 日常运行入口在真实模式下没有先调用 `_ziniao_bridge_ready()`；Bridge 不可用时会在各平台采集阶段失败，之后仍继续分析并尝试发送普通日报，不符合独立故障处理要求。
- 生产目录提交 `5f5edd9` 无法在当前仓库解析，两个目录不是可用同一提交历史直接比较的工作树；将当前仓库改好并不会自动更新现有 LaunchAgent 实际执行代码。
- 生产报告目录现在已经出现 `daily-2026-07-29.md`，且服务已重新注册，说明“立即重载 + 补跑”很可能已由此前操作完成；仍需用文件时间、内容和发送日志确认补跑结果，避免重复发送日报。
- 生产日志保留了 Bridge 无法连接的 Python traceback，但写在通用 `launchd.err.log`；这进一步确认目前没有结构化独立故障日志与专用故障通知。
- 唯一代码源已有可执行 `.venv`，其 `adwatch` 可确认加载当前仓库 `src/adwatch`；`.env` 中数据目录、两家紫鸟店铺 ID 和飞书 Webhook 均已配置。
- 唯一代码源已有独立 `var/` 数据、备份、日志和截至 2026-07-28 的日报；尚无 `daily-2026-07-29.md`。
- 旧目录的 `daily-2026-07-29.md` 创建于 2026-07-30 20:49，内容为真实数据高风险日报；补跑确实在旧目录完成，但仍需确认当前唯一代码源的数据接管方式及是否已发送飞书。

## 2026-08-03 TikTok 临时停采与 0801 补采

- 生产开关为 `ADWATCH_TIKTOK_COLLECTION_ENABLED=false`，启用平台解析结果仅为 `shopee`；关闭状态下不要求 TikTok 店铺 ID，也不再显示 TikTok 现场验收门禁。
- 0801 最新补采只创建了一条 Shopee `collection_runs` 记录，没有创建新的 TikTok 运行记录，证明双入口的平台过滤已在真实运行中生效。
- 执行日汇率成功使用 2026-07-31 最近交易日的 THB/CNY 值，普通日报及故障通知均因 `--no-notify` 标记为 suppressed。
- Shopee 广告 URL 被重定向至 `accounts.shopee.co.th/seller/login`；当前失败属于店铺登录会话失效，不是 TikTok 干扰或总览字段结构变化。完成 Shopee 登录前不得用旧值或模拟值填充 0801。
- 登录恢复后的真实 0801 总览为消耗 381.96 THB、销售额 1782.00 THB、8 单、页面 ROAS 4.67；平台金额展示精度导致直接相除值为 4.6654，必须按项目既有比率容差校验，不能要求两位小数绝对相等。
- 0801 最终采集运行 succeeded，写入 1 条 Shopee 汇总指标，质量报告无隔离数据；普通日报与故障通知均未发送。前端 0801 核心指标与数据库一致。
