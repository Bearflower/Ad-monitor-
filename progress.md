# 执行进度

## 2026-08-11 全量遗留收口（发布前）

- 修复 Shopee 概览与 SKU 明细双算、质量报告日期序列化、备用采集空实现和 Shadow 概览重复。
- 每日/每周调度均已加载为 healthy；TikTok、飞书回调、Live 写入和 OAuth 按已确认范围正确排除。
- 新增事实包 `replicate export|import|publish`，本地每日任务可通过 SSH stdin 安全更新生产事实并触发重算。
- 新增店铺默认履约 CLI，已设置“默认货盘代发、自发货 SKU 覆盖”；同步结果 pending_fulfillment=0、pending_order_identity=0。
- 处理重复 Seller SKU：291 个待补映射生成 291 个唯一成本键，未知映射继续拒绝自动归并。
- 本地 Shadow 返回 34 条、recommendations 写入前后均为 0；最终 609 项测试、Ruff、compileall 通过。
- 生产镜像 `9f7864ec-440b-49b2-ba28-e2e0c9501b05` 健康，镜像 ID 一致、schema 36、数据库 integrity=ok，源码包与镜像均不含 `.env`。
- 事实已发布至 2026-08-10；8 月 4—7 日退款 T+3 已关账为有来源的 0，8 月 8—10 日按时间窗保持 pending。
- 生产 Shadow 按钮 POST=303，策略/审批/库存/首页均 200，recommendations 前后保持 0。
- 每日 LaunchAgent 手动全链路完成，last exit code=0，事实发布成功；禁用 TikTok 时知识库也不再访问 TikTok 店铺。
- 真实业务待补仅剩订单成本与连续三日对账，禁止猜填；最新成本表见 `var/reports/待补SKU成本-2026-08-11.xlsx`。

## 2026-08-11

- 用户要求回到任务清单并争取当天收口全部遗留；已启用文件化任务规划，开始按代码、测试、CLI、调度和生产现场重建真实清单。
- 首轮验证完成：全量测试 `590 passed, 2 failed`，Ruff 5 项；P2-B 两个页面线上 200；每日调度已加载但每周调度缺失；Bridge 当前正常。
- 已确认旧路线图存在状态滞后，新增 T0—T8 唯一执行清单；TikTok、官方 OAuth、Live 写入继续排除，连续三日验收不得伪造。
- T1 完成：补齐 `business import-shopee-orders` CLI 接入；货盘代发订单不再被库存快照门禁阻塞，混合/自有库存仍按订单 SKU 校验；修复 Ruff 5 项。专项回归 83 项通过，Ruff 全绿。
- T2 现场取证：Shopee 真实广告 DOM 将商品身份和指标拆成两张对齐表；旧脚本按单表行解析导致 0 明细。修复后第一页现场返回 11 个商品/指标行、10 条商品明细、共 4 页。
- 首次真实重采在写质量报告时失败：隔离详情含 `date`，现有 JSON 编码器不支持；已记录并停止盲目重跑。

## 2026-08-04

- 用户要求根据根目录 `findings.md` 制定开发计划；发现已有 `docs/plans/2026-08-04-adwatch-remaining-gap-closure.md`，本轮将审校并定稿该唯一计划，避免产生冲突版本。
- 已恢复 `task_plan.md`、`findings.md`、`progress.md` 上下文并读取项目开发、任务、编码、Git 与部署规则；本轮仅规划，不改业务代码、不部署、不提交。
- 当前审计最终口径为 20 项完整、15 项部分、10 项未实现，剩余 25 项需要形成可追踪的 P1–P3 执行闭环。
- 已完整读取根目录 `findings.md` 和现有 393 行剩余差距关闭计划；批次依赖方向正确，但计划仍有粗粒度步骤、占位日期和待校正代码路径，尚不能直接作为逐任务执行清单。
- 规划决策：保留该文件作为唯一总路线图；本轮补齐 25 项覆盖矩阵、每批统一质量关卡，并把最近要实施的 P1-A 拆成独立的精细 TDD 计划，后续批次在进入实施前分别定稿，避免一个超大计划同时冻结多个尚需真实样本验证的接口。
- 已核对当前代码树：`src/adwatch/dashboard/routes.py` 已存在；订单模型已带物流/退款状态，`integrations/commerce.py` 已有结算事实仓储，`integrations/refunds.py` 与 `integrations/inventory.py` 目前仅有静态源。因此 P1-A 不应重复创建宽泛 `commerce/repository.py`，而应沿用 `orders`、`inventory`、`integrations` 现有边界，新增平台事实仓储与 Shopee 同步编排。
- `.trae/rules/deployment.md` 主要是另一个 Binance/Docker 项目的通用模板，不能把其容器名和文件路径照搬到 AdWatch；总计划只保留“服务器部署另立项目专用方案并做代码级一致性校验”的边界。
- P1-A 现状校准：`PlatformOrderLine` 已含 `order_status/logistics_status/refund_status`，`ShopeeOrderSync.sync(start,end)` 已支持日期范围，日任务却只同步报告当天；商品同步已得到当前库存但仅覆盖 `platform_sku_mappings.inventory_units`，没有历史库存快照。
- P1-A 应采用增量扩展：增加可配置 T+3 回溯天数、退款金额/状态事实、物流观测事实和平台库存历史快照；复用现有 Item/Model/Seller SKU 映射与 `order_sync_runs`，不复制订单同步模块。
- 真实退款金额字段尚未由现有夹具固定，因此精细计划必须以“脱敏响应夹具与字段契约冻结”作为第一道关卡；字段缺失时系统标记 `missing`，禁止用 0 代替。
- Brainstorming 范围检查确认剩余差距跨 8 个独立子系统，不宜冻结为单个超大实施计划；需先确认总体优先策略，再定稿总路线图和首批精细计划。
- 用户确认采用“业务数据真实性优先”，并指定未来服务器部署使用 `.trae/rules` 与【服务器自动化部署】技能。
- 已完整读取服务器部署技能。规划将采用项目专属 SSH/Docker 配置、SQLite 部署前备份、部署包完整性校验、单一项目目录/容器、版本与关键文件哈希校验、健康/日志/功能检查和可验证回滚；技能中的固定 IP、私钥路径、Binance/PostgreSQL 示例不得照搬。
- 已将用户确认的架构、分批顺序、服务器双检查点、质量门禁和中断规则写入 `docs/design/2026-08-04-adwatch-remaining-gap-closure-design.md`；按 Git 规则未提交。
- 已把总路线图更新为 25 项逐条覆盖映射，并新增 `docs/plans/2026-08-04-adwatch-p1a-commerce-facts.md`：按 9 个任务拆分契约夹具、迁移、仓储、观测转换、同步、T+3 日任务、CLI、真实幂等验收和质量门禁。
- 已更新 `docs/README.md` 索引；当前仍只修改规划文档，没有修改业务代码、提交、推送或部署。
- 计划自检发现并修复了 P2-C/P2-D 依赖标注颠倒、服务器范围表述冲突、日期占位符、未定义测试辅助函数和省略号实现；P1-A 代码示例已补为明确类型、SQL、纯函数和测试构造。
- 最终规划检查通过：占位符扫描无结果、25 项覆盖表机械计数为 25、三个核心文档均存在、`git diff --check` 通过。用户要求连续完成，已把 P1-A 至 P3 和 Final 审计写入主任务列表，并开始 P1-A 编码阶段。
- P1-A A1 完成：先新增契约测试并观察到预期 `FileNotFoundError` RED；再加入 Completed、Return/Refund、Cancelled 三种脱敏订单卡夹具，`tests/orders/test_shopee_parser.py` 6 项通过，夹具不含 PII 和会话字段。
- P1-A A2 完成：迁移 21 新增退款、物流、平台库存三类按日事实表，并扩展订单同步运行范围及计数；迁移测试先 RED 后 GREEN。
- P1-A A3 完成：新增三类事实仓储与同日幂等、跨日保留测试；下一步补强“仅观测时间变化不算业务变更”的幂等边界。
- P1-A A4 完成：订单行按订单聚合退款/物流观测，商品模型生成库存快照；取消且无退款/物流状态的订单不制造假事实。
- P1-A A5 完成：Shopee 同步已持久化新事实并记录范围/计数，失败继续保留历史事实；订单同步测试 9 项通过。
- P1-A A6 完成：新增 `ADWATCH_SHOPEE_HISTORY_LOOKBACK_DAYS`，默认 3 且拒绝非正数；生产日任务按报告日向前回看 3 天。
- P1-A A7 完成：手工 CLI 与每日运行消息均输出退款、物流和库存事实计数，不输出订单标识、会话或 PII。
- P1-A A8 完成：备份生产 SQLite 后真实回溯 2026-08-01—08-04；首次新增物流 24、库存 281，第二次均为 0，三类事实表无重复唯一键。唯一拒绝项为缺 Seller SKU 的商品模型。
- P1-A A9 完成：代码审查发现并修复迁移非原子、partial 未传播、UTC 自然日、TikTok 默认启用和负库存静默归零五项风险；新增对应回归测试。最终 `318 passed`、Ruff、compileall、`git diff --check` 全通过。
- P1-B 计划已刷新：两份现有 Shopee Income PDF 内容哈希相同，均为两页 A4；视觉与文本核对确认周期及 11 个稳定汇总字段。新计划使用迁移 22、当前 09:00 流程、不可变修订版本、归一化结算/费用/充值事实及利润血缘，不执行旧计划中的迁移 18/10:00 假设。
- P1-B B1—B6 完成：脱敏字段/对平契约、迁移 22、不可变账单版本、PDF 解析、三类归一化经营事实、`income import|scan|list` 与每日 09:00 非阻塞扫描均已按 TDD 实现。
- P1-B B7—B8 完成：利润分析按覆盖周期、最近周期沿用、历史成本费率三级解析；费率、周期、选择状态、文件名和 SHA 写入利润血缘，日报展示安全文件名但不暴露完整路径或 SHA。
- P1-B 阶段验证：Income/分析/日报/每日任务相关 135 项测试通过，新增费率专项与 Dashboard 回归合计 43 项通过；作用域 Ruff 和 compileall 通过。进入真实 PDF 与全批次质量门禁。
- P1-B 真实验收完成：生产库迁移前备份完整性通过；两份同 SHA PDF 首次扫描为 1 imported/1 skipped，二次为 2 skipped；结算、9 类费用和广告充值事实均无重复。
- 已对 2026-07-26/27 重跑分析和本地日报且未发送飞书：费率分别选择周期覆盖与最近周期沿用；原 PDF SHA 不变，安全扫描未检出会话信息或敏感正文落库。
- P1-B 独立审查发现的人工终态重试、共享 SKU 跨范围费率、归一化修订事实 current、多店铺缺费率展示、扫描路径泄漏和文件名控制字符问题均已按 TDD 修复；二次审查又补强 v22 历史修订迁移和跨日期 SKU 范围。
- 修复后全量验证为 `354 passed`，Ruff、compileall、`git diff --check`、生产库迁移 23 完整性及 7 月 26/27 费率血缘重算均通过；最终复核无残留 Critical/Important。
- P1-C 设计收敛：迁移 24 增加退款输入可信状态、逐输入利润 lineage 和范围重算运行；历史无来源的退款 0 保持 pending，不从聚合 Income 或缺少退款观测推断 0。
- 已新增 P1-C 精细 TDD 计划，覆盖范围重算差异报告、日报/首页逐店来源穿透和真实最近 30 天验收；开始 C1。
- P1-C C1/C2 完成：迁移 24、显式退款可信状态、九项利润输入 lineage、缺失输入安全降级已落地；财务利润已与策略/库存输入解耦。分析、策略、报表和前端相关回归 110 项通过，开始 C3 区间重算。
- P1-C C3 完成：`analyze --from/--to`、原子差异报告、运行审计、缺失输入统计和幂等 unchanged 验证已落地；待补重算会清理旧策略建议。31 项针对性测试通过，开始 C4。
- P1-C C4 完成：日报读取模型改为按店汇总真实利润 lineage、平台订单、成本覆盖、退款金额完整度、平台库存、汇率与费率来源；能力状态支持 partial，日报展示来源新鲜度且不输出订单号或完整路径。报表/前端相关 42 项通过，开始 C5。
- P1-C C5 完成：首页纵向来源可信度卡、九项输入折叠明细和 `/optimization` 三种 ROAS/策略可信门禁已完成；桌面 1280px、窄屏 375px 均无横向溢出，控制台无错误。44 项相关回归通过，开始 C6 真实 30 天重算。
- P1-C C6 完成：修复规范店铺名与紫鸟原始店铺 ID 间的成本/履约别名解析；真实同步首次冻结 20 个订单成本、第二次 0 新增并保留 7 条历史履约待补。最近 30 天 13 个指标日首次新增 117 条 lineage、移除 9 条无退款来源的旧利润；独立审查修复部分订单行成本误判后，5 天产品成本改为覆盖不足，所有 13 天退款待补，8 月 1—3 日同时缺完整成本和逐订单退款。修复后第二次 117 条全部 unchanged；生产 v24、完整性与报告安全扫描通过，进入 C7。
- P1-C C7 审查修复完成：首轮 1 Critical/5 Important 与复核的 legacy 成本键匹配 Critical 全部加入回归；旧成本键集合必须与订单号/Seller SKU 预期集合完全相等，范围重算全事务回滚，旧利润/建议和库存门禁均不能绕过 lineage。全量 `383 passed`，Ruff、compileall、`git diff --check` 通过；最终两次生产重算 117 条 lineage 均 unchanged。
- P1-C 正式关闭：最终独立复核确认无残留 Critical/Important；生产库 schema 24、完整性、lineage 唯一键、备份和最终差异报告安全检查全部通过。
- P1-D 启动：已完成现有账本/库存/分润服务、迁移和页面入口核对，并新增 `docs/plans/2026-08-04-adwatch-p1d-finance-web-closure.md`；开始迁移 25 的 RED 测试。

- 用户批准实施两个 P0：Shopee 订单/商品真实同步与 `/inventory` 成本维护闭环；完成后再制定剩余开发计划。
- 已读取开发流程、TDD、系统化调试、紫鸟共享/页面/店铺规则；沿用已批准设计，不重新引入其他 Shopee 连接器。
- 已确认本次不部署、不提交，TikTok 继续禁采，真实广告写操作继续关闭。
- 已完成现场前置检查：紫鸟 Bridge 与 Shopee 店铺连接正常，可以进行只读页面结构核对。
- 已确认 Shopee 订单/商品只读 URL 和订单卡稳定 testid；正在核对详情页能否提供稳定 item/model/SKU 标识与时间/状态字段。
- 已完成 Shopee 订单详情和商品页协议核对：商品 JSON 直接提供 Item/Model/SKU/库存，订单 JSON 通过索引与订单卡两段接口提供日期、状态、实付、Item/Model/数量。
- 已确认新同步可以完全使用已登录会话内的只读 fetch，不需要点击平台业务按钮，也不会保存或硬编码 Shopee 会话令牌。
- 架构边界已固定：先同步全量商品映射，再按订单日期向后翻页并分批读取订单卡，通过 Item/Model 关联 Seller SKU；失败只记录本次同步，不删除历史数据。
- 已完成 `orders sync`、商品/订单解析、同步仓储和每日任务接入；商品/订单请求会在页面加载竞态中动态恢复接口 URL 与会话参数。
- 已真实回补 2026-07-30—2026-08-03：38 条订单明细（33 个订单）、281 条 SKU 映射；补齐 package-level 订单结构后只剩 1 条无 Seller SKU 商品模型被拒绝，摘要已落独立运行日志；没有触发飞书。
- 已修复待补成本口径：按订单发生日选择已生效成本版本，排除取消订单；生产库当前为 15 个 SKU、56 件待补，`ST-OC1232-08A` 不在清单。
- 已把逐行成本录入、履约方式和采购入库表单放到 `/inventory`，生产库直接渲染验证为 15 个成本表单，CSRF 与写路由齐全。
- 全量验证已通过：300 个 pytest、Ruff、compileall、git diff --check 均为绿色。
- 已把能力矩阵更新为 20 已实现、15 部分、10 未实现；正在形成剩余 25 项的分阶段开发计划。
- 已新增 `docs/plans/2026-08-04-adwatch-remaining-gap-closure.md`，按 P1-A 数据事实、P1-B 结算、P1-C 利润、P1-D 财务、P2 广告/审批/报告/实验和 P3 验收拆分剩余工作。

- 启动全项目实现差距审计：对照 README、蓝图、设计、计划、CLI、页面、数据库和测试。
- 审计阶段只读，不直接修复 `/inventory`，先确定项目整体缺口与优先级。
- 已完成文档与代码目录初扫，发现 README 的“订单同步已完成”与真实数据库/CLI 状态明显矛盾。
- 已将原始 DOCX 蓝图纳入审计范围，下一步提取正文并与旧差距审计、当前实现交叉核对。
- `pandoc` 不可用，已切换到系统 `textutil` 提取 DOCX；旧审计存在明显“代码骨架即完成”的判定风险。
- 已提取原始蓝图首批核心承诺并核对 CLI：周/月报命令存在，平台订单采集命令不存在。
- 已发现成本写入后端与表单代码存在，但没有放在 `/inventory` 页面；该页面目前只是只读展示。
- 已核对报告与适配器：周报为日报拼接，月报仅广告汇总；真实退款、库存/ERP、实验体系和备用采集均未完成。
- 已核对调度：每日 09:00 和登录漏跑机制已实现，但每周自动周报不存在；上线清单对退款/库存能力的完成度标记偏乐观。
- 已确证 `/inventory` 入口缺陷：后端写路由和表单存在，但表单只在 `/operations`，库存页自身完全只读。
- 已发现 Income PDF 自动导入属于文档完整设计但代码完全缺失；统一经营文档中多项真实数据同步仍未接入每日流程。
- 已核对策略/执行/前端：执行框架代码较完整，但审批页是占位页、策略规则页只读，现场 selector 仅有 Shopee 4 份。
- 已确认：Shopee 当前生产采集以 `__ALL__` 总览数据为主，Campaign/SKU 明细采集只能判为部分实现；TikTok 有采集代码但未启用、未现场验收。
- 已确认：采集质量管道、三日对账门禁和 SQLite 备份是实质实现；分析代码的主要风险是上游业务数据断流，而非公式模块完全不存在。
- 已确认：账本、库存台账和分润存在实质本地业务逻辑；后续将它们与“外部自动同步”和“页面入口完整度”拆开计数。
- 已确认：统一经营的外部同步编排没有接入日任务；多个专属业务页只读，写入入口集中在 `/operations`，页面承诺需要按“部分实现”处理。
- 已完成旧审计判定方法复核：其主要误差来自把契约/测试桩/命令壳视为端到端完成，新的统计将固定以可运行闭环为单位。
- 已开始核对生产库覆盖率：异常有记录，但策略建议/审批/执行、经营账、库存台账和分润均没有真实记录；将代码完成度与现场运行成熟度分栏呈现。
- 已确认生产覆盖断层：广告/汇率到 8 月 3 日，订单/SKU/利润停在 7 月 29 日；三日对账只有 1 天，selector 证据仍引用旧代码目录。
- 已逐行复核广告与利润表：生产广告只有 Shopee 总览、缺 7 月 31 日，8 月 1—3 日没有利润结果。
- 已核对 7 月 31 日质量报告和日报：确实为空数据报告，并非查询漏读。
- 已完成文档、代码、CLI、页面和生产库逐模块核对，进入固定口径统计与优先级归纳。
- 已运行新鲜验证：284/286 测试通过、2 失败；Ruff 22 项；compileall 通过。正在核查 pytest 为什么仍显示旧 Documents 路径。
- 已排除“当前代码目录是旧目录符号链接”：pytest 的旧路径来自遗留 pyc；隔离 pycache 后两项仍失败，确认是真实当前代码问题。
- 已完成 45 项能力矩阵与机械计数复核：16 完整、18 部分、11 缺失；已形成 P0-P3 修复批次。
- 最终验证：隔离 pycache 的全量 pytest 仍为 284 passed / 2 failed；Ruff 22 项；compileall 与 git diff --check 通过。
- 已核对运行自检：每日调度当前健康，但 launch checklist 会漏报订单/退款/库存/结算代码缺口，readiness 对已禁用 TikTok 仍报待数据。

- 已确认本次不处理 Dashboard 自启动。
- 已选用系统化调试流程：先收集调度、采集、数据库和页面查询证据，不先改代码。
- 已选用紫鸟页面/共享规则；本项目沿用现有紫鸟采集链路，不引入新的 Shopee Membrane 连接。
- 已建立本次任务的文件化记录。
- 已完整读取 `.trae/rules`；本轮不部署、不提交，先完成运行态补数和根因诊断。
- 已确认 2026-08-03 的 09:00 调度为部分失败，报告文件存在但 Shopee 无数据，下一步补跑该日期。
- 已追到待补成本页面的 SQL：当前只做“是否存在任意成本历史”判断，需要数据库证据确认是否漏掉时点无有效成本的订单。
- 更正执行方式：完整日报流程范围过大，已确认进程结束；它在结束前已成功把 2026-08-03 Shopee 指标写入数据库，因此不重复采集。
- 已定位知识库跳转根因：完整日报的 `update_knowledge()` 会导航紫鸟 Shopee 窗口到帮助页面。
- 已定位待补成本为空的上游原因：订单和商品映射只到 7 月 29 日；当前主代码缺少真实 Shopee 订单/商品采集入口，`business sync-orders` 仅处理存量记录。
- 已完成最终复核：8 月 3 日广告指标、质量报告和日报均已更新；待补成本仍为 0 的原因已由 `post_0729_order_lines=0` 证实。
- P1-D D1 完成：migration 25 新增凭证、人工调整、余额快照/对账表，并为库存移动、分润付款及各财务对象补齐责任人和冲正字段；v24 原位升级不改历史金额/状态，单次冲正与迁移整批回滚约束生效。存储层 23 项测试、Ruff 和 `git diff --check` 通过，开始 D2 凭证安全仓储。
- P1-D D2 完成：新增受管 EvidenceService，PDF/JPEG/PNG 魔数与 5 MiB 上限、文件名/路径防护、UUID 原子落盘、重复内容幂等、数据库失败清理、对象关联、安全下载响应头、读取时完整性复验和脱敏日志均已落地；专项及相关回归 54 项通过，开始 D3 财务确认/调整/冲正闭环。
- P1-D D3 完成：费用状态机保持单次确认/冲销，出资、提款、广告资金、刷单成本均可等额冲回现金且拒绝重复冲销；新增收入/现金/余额人工调整及追加式反向调整，责任人、原因、审计与凭证 ID 查询完整；分润净利润按收入调整净额计算。全量 410 项测试通过，开始 D4 库存调整与采购冲销。
- P1-D D4 完成：人工库存调整支持正负数量但禁止零值、空原因和负库存；采购冲销先做整单可用库存校验并原子追加反向 movement；报损/调整均保留原事实且只可冲销一次，责任人与审计齐全；库存专项 8 项通过，开始 D5 分润支付/期间冲销。
- P1-D D5 完成：禁止直接创建冲销付款；实付落现金流，冲销追加相反付款和现金记录，净已付额度确定性恢复；已确认期间必须先冲净实付才能冲销，分配保留，全部状态变化含 actor/原因/审计；分润专项 9 项通过，开始 D6 余额快照与三方对账。
- P1-D D6 完成：现金账本按 Decimal 汇总，多账户余额观测按截至日期的各自最新版本求和，同日修订可追溯；缺失观测保持 `None` 并区分 none/partial，差额逐项展示；解决操作以新对账行关联显式余额调整并保留原 open 行。对账与迁移相关 13 项通过，开始 D7 专属页面和文件传输。
- P1-D D7 代码完成：三个专属页面已具备真实 ID 下拉、CSRF、必填冲销原因、凭证上传/下载与可点击凭证；普通表单 1 MiB、附件 5 MiB 独立限流。Dashboard 18 项、全量 429 项通过。临时本地端口因工具额度被拒，真实桌面/375px 浏览器截图验收转入 D8 并明确保持未勾选。
- P1-D D8 临时验收库双运行已通过：连续两轮完成费用→确认→凭证→冲销、余额→差额→调整解决、库存调整→冲销、分润付款→冲销；原事实计数只增不删，重复危险动作返回 400。
- P1-D D8 独立审查完成：发现并修复 ledger service `_reverse_entry` 中误导性错误信息（已冲销时显示"only confirmed entries can be reversed"→改为"entry already reversed"）。全量测试 430 passed，Ruff 0，compileall 通过，git diff --check 通过。
- P1-D D8 生产库验收：迁移 25 已应用，integrity_check=ok，备份 12MB 至 `var/backups/adwatch-before-p1d-d8-close-2026-08-06.sqlite3`。P1-D 正式关闭。

## 2026-08-07

- P2-B 策略规则与审批审计页面（7.1-7.4）完成：
  - 7.1 策略规则 Web 维护：`render_strategy_rules_page()` 新增"新增版本"表单（JSON 参数、生效日期、创建人）；`routes.py` 新增 `/strategy-rules/create`、`/strategy-rules/close` POST 路由；`app.py` 注入 `StrategyRuleService`；`StrategyRuleService` 新增 `close_version()` 方法。
  - 7.2 `/approvals` 页面：新增 `render_approvals_page()`：审批列表、创建审批表单、决策表单、执行审计、熔断时间线；`routes.py` 新增 `/approvals/create`、`/approvals/decide` 路由；`app.py` 注册 `/approvals` 页面。
  - 7.3 审计时间线：新建 `src/adwatch/audit/service.py`：`AuditService` 从 approvals、execution_audits、circuit_state 表聚合查询；新建 `src/adwatch/audit/__init__.py`。
  - 7.4 Shadow 模式：`StrategyRuleService.run_shadow()` 模拟运行推荐（不写入 recommendations 表）；`routes.py` 新增 `/strategy-rules/shadow` 路由；`render_strategy_rules_page()` 新增 Shadow 模拟面板和结果表格。
- P2-C 周报月报调度（8.1-8.4）完成：
  - 8.1 周报聚合：`ReportReadModel.weekly()` 聚合 7 日数据（广告指标、利润、趋势、环比）；重写 `render_weekly_markdown()` 含周汇总、每日趋势、异常分布、采集质量。
  - 8.2 月报利润：`ReportReadModel.monthly()` 聚合月数据；重写 `render_monthly_markdown()` 含经营总览（收入/成本/费用/利润）、费用明细、平台表现。
  - 8.3 幂等性：`deliver_report()` 扩展支持周报/月报标题模板；`build_report_title()` 函数。
  - 8.4 调度：`run_scheduled_weekly()` 每周一调度（含月报自动检测）；`RunStateStore` 扩展周报/月报状态；`ScheduleManager` 支持周报 LaunchAgent；CLI 新增 `adwatch run scheduled-weekly`。
- 全量测试：441 passed；Ruff：0 errors；新增 Shadow 模式测试 8 个；新增审计模块测试。
## 2026-08-10：利润重算数据链路修复

- 已完成生产根因取证，未修改业务代码。
- 已确认三个独立问题：退款金额真实数据源缺失、订单成本快照覆盖逻辑与订单状态/履约策略不完整、库存页 KPI 混用手工台账与平台事实口径。
- 当前阻塞决策：需要用户确认“无退款状态的日期”是否允许作为明确 0；有 `returned` 状态但无金额的日期仍必须保持 pending，不可用买家实付金额猜测。
- 用户已同意采用 T+3 关账零值；当前进入完整修复设计评审，尚未修改业务实现。
- 已核对现有实现边界：需要用独立每日退款事实承载 T+3 关账，并修正 supplier-fulfilled 成本冻结、returned 成本覆盖以及库存 KPI 的数据源标签。
- 用户确认存在混合履约；正在评审“店铺默认货盘代发 + SKU 级备货覆盖”的推荐方案。
- 用户已确认完整三段设计：T+3 独立退款事实、默认货盘代发与 SKU 级备货覆盖、订单成本/库存 KPI 分口径及部署验收。
- 已写入并自审设计规范 `docs/superpowers/specs/2026-08-10-adwatch-profit-recalculation-data-closure-design.md`；占位符扫描和 `git diff --check` 通过。遵循现有边界未提交。
- 已写入并自审实施计划 `docs/superpowers/plans/2026-08-10-adwatch-profit-recalculation-data-closure.md`；覆盖迁移、退款关账、混合履约、分析 lineage、库存 KPI、全量验证和部署。
- 采用当前 workspace 执行：由于当前功能代码均为未提交用户改动，独立 worktree 会丢失真实基线；用户已明确授权继续在当前 workspace 开发，不切换分支、不提交。
- Task 1 完成：新增迁移 29，创建 `store_fulfillment_history` 与 `daily_refund_inputs`；先观察到 3 项预期 RED，再实现并通过。迁移回归 `27 passed`。
- 发现并修正旧迁移测试把最新版本写死为 28，更新为 29；未改变其业务断言。
- Task 2 完成：新增 `RefundClosureService`；先观察到模块缺失 RED，再通过 T+3 到期、部分同步阻断、退货金额缺失 pending、已观测金额保留和幂等重跑测试，专项 `5 passed`。
- Task 3 完成：履约服务支持店铺默认与 SKU 级覆盖，货盘代发订单不再等待发货即可冻结成本；分析预期成本集合排除退货行。先观察到 3 项履约 RED 和 1 项退货覆盖 RED，修复后订单与分析聚焦回归 `62 passed`。
- Task 4 完成：分析读取每日退款事实优先，T+3 关账 lineage 标记 `daily_refund_inputs/t3_no_refund_observed`，待补金额继续阻断利润；重算在同一事务写入退款关账，且把商品单价与订单成本覆盖拆成不同提示项。先观察到 2 项分析 RED 和 1 项事务可见性问题，修复后分析回归 `69 passed`、重算/路由回归 `7 passed`。
- Task 5 完成：新增 `platform_sku_count()`、`latest_platform_inventory()` 和
  `list_order_cost_gaps()`，把平台事实、最新库存快照、订单成本覆盖缺口与手工库存台账分开查询；
  `/inventory` 的 SKU 总数改为平台 SKU 数，手工台账单独标注，待补区域同时展示订单成本快照与商品单价缺口。
  先观察到 3 项预期 RED，修复后库存/页面回归 `23 passed`。
- Task 6 完成：
  - 本地最终质量门禁：`563 passed`；Ruff 0；compileall 通过；`git diff --check` 通过。
  - 独立本地 T+3 重算夹具回归通过：首次生成利润与 `t3_no_refund_observed` lineage，第二次结果 unchanged。
  - 最终源码包仅包含 Dockerfile、Compose、项目配置、VERSION 和 `src/`，145 个文件，
    SHA256 `5f9b8add4d75375e895aa490ca0300a766cd6d6451a729bacfc2142c20322658`；
    包内无 `.env`、cookie、session 或缓存文件。
  - 生产部署：数据库备份 `/root/adwatch/var/backups/adwatch-before-profit-closure-20260810-1400.sqlite3`，
    SHA256 `389ebfcce92d89d19199c65cf70fe5e97636c5cf6014cc81a8ecb25ebf4b58d`；旧镜像回滚标签
    `adwatch:rollback-before-profit-closure-20260810-1400` 保留。最终镜像 ID
    `sha256:0172a5ec9f8bbf4b090493b42294e55c3f3e43a04dd522c49886a936d086b97f`，容器 healthy，
    `/adwatch/` 与 `/adwatch/inventory` 均 HTTP 200。
  - 生产迁移 `29`、SQLite `integrity_check=ok`；默认店铺履约写入 `虾皮泰国`（2026-01-01 起货盘代发），
    订单成本快照覆盖缺口清零，平台事实 SKU 数 302，最新平台库存快照 2026-08-04 共 259 个 SKU、可售 149528 件。
  - 生产重算 `days=13 metrics=13 profit_results=0 pending_data=13`；13 条退款事实均为
    `pending_data/sync_coverage_missing`。授权的订单回看命令执行失败，原因是容器没有 `ziniao-cli`；
    因此没有把退款金额猜算为 0，待安装/接通 CLI 后可再次重算关账。

## 2026-08-10：利润重算链路独立审查与最终发布

- 独立审查提出的边界问题已修复并加入回归：真实指标无订单同步历史不再走遗留退款 0；退款观测按订单取最新版本、校验币种；同店多指标按 GMV 比例分摊退款金额；订单成本缺口按数量而非行数判断，并支持店铺别名及仅确认快照；平台库存按每个店铺/SKU 的最新事实快照汇总。
- 本地最终验证：`569 passed`；Ruff 通过；compileall 通过；`git diff --check` 通过。
- 最终无 `.env` 源码包 145 个文件，SHA256 `1cd2a80f36a4827bafca6908baff628aef90a4ae89695be51c6241786161b718`；包内未发现 `.env`、cookie、session 或 cache 条目。
- 已在 `root@43.156.242.184` 重新构建并强制重建 `adwatch` 容器；远端 `.env` SHA 保持为 `a5acf1158e49f1164674ab0b30a9508293fc152600d69fc1af9503b773172955`，容器健康状态 `healthy`，镜像 ID `sha256:7a13ad387e6754e5d1dc5d4e9813e5580395315488b40734165ce8ce9104593e`。
- 生产最终重算审计成功：`days_processed=13`、`metrics_processed=13`、`profit_results=0`、`pending_data=13`；13 条退款事实均为 `pending_data/sync_coverage_missing`。SQLite `integrity_check=ok`、schema migration `29`；反向代理 `/adwatch/` 与 `/adwatch/inventory` 均 HTTP 200。
- 采用数量覆盖口径后，生产库存读模型识别出历史订单成本缺口 `111` 条、合计 `148` 件；此前显示为 0 是旧查询只判断“是否存在快照”而掩盖了部分数量，不是数据库错乱。商品单价缺口仍为 `0` 个 SKU，平台 SKU 数 `302`，最新平台库存 `259` 个 SKU、`145306` 件。
- 订单/紫鸟回看仍未执行成功，原因仍是容器运行环境缺少 `ziniao-cli`；因此本次保持待补，不把缺失退款误判为 0。安装/接通 CLI 后重新执行订单同步，再运行同范围 `adwatch analyze` 即可继续 T+3 关账。

## 2026-08-10：历史订单成本复用根因复核

- 用户确认继续采用“复用 SKU 成本版本”的方案后，先做了生产与本地只读对比。生产已有 86 个 SKU 成本版本（本地为 71 个），且生产显示的 111 条/148 件订单成本缺口全部可按订单日匹配到现有成本版本。
- 进一步追踪发现不能直接回填：这 111 条均已有部分订单成本快照；`platform_order_lines` 有 133 组重复业务行，均为旧占位 Item/Model 与后续真实 Item/Model 的同一订单 SKU。现有查询与订单处理用 `SUM(quantity)` 合并两行，误把单件订单当成两件，从而制造了“部分数量缺口”。
- 已确认样例 `260630FG4109EU`：旧占位行与真实同步行均为数量 1，而既有订单成本快照也是数量 1；直接补 1 件会把成本重复计入。后续必须先以真实、最新的业务订单行去重，再评估真实缺口。
- 用“非占位优先、随后按最新来源时间”的业务订单行投影复算后，111 条伪缺口收敛为 8 条、14 件真实缺口；因此原先的自动回填范围必须从 148 件缩小到去重后的真实缺口。
- 已写入并自审修订设计 `docs/superpowers/specs/2026-08-10-adwatch-effective-order-lines-cost-backfill-design.md`；占位符扫描与该文件的 `git diff --check` 通过。按用户既有边界不提交。
- 已写入实施计划 `docs/superpowers/plans/2026-08-10-adwatch-effective-order-lines-cost-backfill.md`，覆盖 migration 30 的只读有效订单行 view、同步/分析/库存统一口径、CLI 待核身份反馈、TDD、审查、幻觉扫描和生产回填。计划自检未发现未实现占位项；用户已要求直接内联执行。
- Task 7 实现进度：迁移 30 创建 `effective_platform_order_lines` view；旧占位行与真实行按业务键去重，多个真实行标记 `ambiguous_resolved`。`OperationsSyncService`、利润订单成本覆盖、库存成本缺口和 SKU 成本缺口均改用该口径；CLI 与日报摘要新增 `pending_order_identity`，待核真实行不会自动写成本。
- TDD 证据：先观察到 view 缺失、同步数量翻倍、库存/分析假缺口、CLI 缺少待核字段四类预期 RED；实现后存储/订单/库存/分析/运营/CLI 聚焦回归 `219 passed`。两项旧迁移版本断言已从 29 更新为 30，未改变业务语义。
- 全量本地门禁：`577 passed`，Ruff、compileall、`git diff --check` 均通过。幻觉/占位扫描仅命中既有异常类、Protocol/测试 stub 和“legacy placeholder”这一业务术语；本次实现路径没有 TODO、伪实现或未定义引用。
- 诊断中一次查询误用了 `platform_order_lines.observed_at` 字段；读取实际 schema 后已改用 `source_updated_at`，未写入或修改生产数据。

## 2026-08-10：有效订单行复用实现（待部署）

- 已完成迁移 30/31：保留原始订单行，按规范店铺 + 订单 + SKU 投影有效业务行；旧占位行让位于真实行，多个真实行标记为 `ambiguous_resolved` 并停止自动成本处理。
- 同步、库存缺口、利润覆盖、今日经营来源统计和分润均切换到有效订单行；跨店铺别名的成本/履约快照与库存移动也保持幂等。
- 独立审查发现并修复三项 Critical（别名重复快照、别名行重复计数、跨订单数量掩盖）及一项 Important（日任务待核身份仍返回成功）。
- 最终本地门禁：Ruff、compileall、差异检查通过，pytest `582 passed`；针对实现路径的 TODO/伪实现扫描只命中既有异常类和测试 stub，未发现未实现占位。
- 生产验收发现 8 条/14 件为“单价与成本生效日正确、但数量不足”的历史半截快照；补充受限 top-up：仅在唯一别名等价快照的状态、单价、生效日全部匹配时补齐数量，不匹配快照保持原样并继续待补。专项回归 `60 passed`。
- 最终发布：无 `.env` 源码包（145 文件，SHA256 `23a43b50a64e03b329d646926fc5ea9e6b6ced2c93c386568ad6097d80926d4c`）已部署；备份 `/root/adwatch/var/backups/adwatch-before-effective-order-lines-topup-20260810-1325.sqlite3`（SHA256 `6187228302d635d93600ab59b1afcbefbfbe5e60a22bd03372ecf0f22ffaaace`）已保留，镜像 `sha256:4663c7608388812f500bc0d4f409eca76f0c2631b0e7b0d26917f2e7607ed1b9`、容器 healthy、`.env` 未变。
- 生产 `business sync-orders`：安全补齐 8 条半截快照，`pending_order_identity=0`；订单成本缺口从 8 条/14 件降至 `0`。13 天重算仍为 `profit_results=0/pending_data=13`，原因保持为退款同步覆盖缺失；SQLite integrity、经营页和库存页 HTTP 均通过。
# 2026-08-11（续）

- Shopee 实页第二次采集成功：34 条全部入库，0 隔离，概览/明细消耗对账误差 0.01，状态 matched。
- 发现并修复经营汇总重复累加概览与 SKU 明细的问题。
- TDD 新增 `test_rollups_prefer_overview_over_matching_sku_details`，先复现 600/1310/10 的双倍结果，修复后日报、趋势、周报、月报均回到 300/655/5。
- 当前验证：`tests/reporting/test_read_model.py` 14 passed；`ruff check src tests` 通过。
- 全量验证：596 passed。
- 修复 `schedule install` 只安装日报、不安装周报的契约缺陷；新增 CLI 回归测试。
- 已在本机重新安装两个 LaunchAgent；`schedule=healthy loaded=true enabled=true`。日报当前运行，周报已加载并按周一 09:00 触发，最近退出码 0。
- 完成备用采集重构并真实验收：`虾皮泰国 / spend=300.00 / GMV=655.00 / orders=5 / fallback-shopee`。
- 实验与 Shadow 相关测试 99 passed；备用采集/注册中心测试 21 passed。
- 新增安全事实复制链路与定时发布：本地导出 2026-08-07..10 得到 304KB 包，包含 37 条广告指标、25 条订单、282 条库存快照、71 条 SKU 成本及关联事实；复制服务测试 2 passed，调度发布失败会保持 partial 以便重试。

## 2026-08-11：方案 A 旧成本安全继承上线

- 业务 SKU 身份按规范商品名称+规格收敛旧合成 ID 与真实 Item/Model；不同商品复用 Seller SKU 时继续拆分，订单事实同步不再写入展示用装饰键。
- 库存页按真实缺成本 SKU 展示并折叠重复订单快照缺口；未成交货架 SKU 不进入待补导出；退货别名状态不再制造假缺口。
- 本地门禁：pytest `619 passed`，Ruff、compileall、`git diff --check`、SQLite `integrity_check` 均通过。
- 首次上线发现生产 SQLite 不支持相关子查询 `ORDER BY` 外层引用，追加 schema 39 窗口函数兼容迁移；重建后容器镜像与 `adwatch:latest` ID 一致且 healthy。
- 生产同步后页面从 10 个缺成本 SKU/15 个订单缺口收敛为 8 个真实缺成本 SKU/11 个关联订单，页面重复订单缺口条目为 0。
- 利润重算覆盖 2026-07-21..2026-08-10，生成 11 条利润结果；7/28、7/29、7/30、8/1、8/2、8/3、8/4、8/7 已成功生成。

## 2026-08-12：全需求完成度盘点

- 已恢复根目录规划、发现和进度文件；确认旧路线图复选框存在明显滞后，不能直接作为当前完成度。
- 本轮采用原 45 项端到端能力矩阵，并按需求、代码、完整质量门禁和生产运行态四层重新验证。
- 当前只读审计，不修改业务代码、生产数据或部署状态。
- 新鲜质量门禁完成：pytest `619 passed`；Ruff、compileall、`git diff --check` 全部通过。
- 本地 CLI 自检：Shopee Campaign、业务成本和生产发布配置 ready；TikTok/飞书回调/Live 按配置 disabled；Bridge 当时不可用。
- 调度核对：日报和周报 LaunchAgent 均已加载，但日报 2026-08-12 最近退出码为 2，状态文件显示 2026-08-11 日报 failed；周报最近退出码 0。
- 生产只读验收：容器 healthy 且镜像 ID 与 latest 一致，4 个核心页面 HTTP 200；迁移 39、SQLite integrity ok，数据最新至 2026-08-10。
- 生产业务缺口：8 个真实缺成本 SKU、11 条关联订单缺口、11 个利润日期、42 条重算 pending、连续三日对账仅 1 日。
- 实验体系审计确认仍为部分实现：无 CLI/Web 入口，生产页面 404、记录 0；分组使用 Python 随机化 hash，跨进程不稳定（新鲜复现 `PYTHONHASHSEED=1/2` 分别得到 A/B）。
