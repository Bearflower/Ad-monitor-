---
id: Cross_Border_Electronic_Commerce-20261013-shopee-report-hold-hidden
类型: issue
项目: Cross_Border_Electronic_Commerce
标题: 日报把 hold 保持方案过滤掉，41 条保持明细全部不可见
日期: 2026-09-16
标签: [bug, report, 需求符合性]
任务标识: shopee_codex_cutover_2026-09-16.md
状态: 已解决
审查: 已审查
---

# 日报把 hold 保持方案过滤掉，41 条保持明细全部不可见

## 现象
2026-09-16 当日真实联调发布批次 `codex-2026-09-16-d6264af449934d44ba964ca650602822`（数据日 2026-09-15，41 条建议全为 `hold`）。Markdown 日报把 hold 从建议区过滤掉，只输出「今天没有需要修改的广告」+「今日另有 41 个活动无需操作。」，41 条保持方案的名称、依据、核查事项全部不可见。

## 根因
旧 `src/adwatch/optimizer/report.py`（第 103-113 行）在渲染建议小节时过滤掉 hold，与需求文档第 136、152 行（保持方案不被隐藏、要求保持方案可见）以及第 70 行（保持不动必须解释观察或核查什么，不能一律称为「无需操作」）冲突。

## 解决
`src/adwatch/optimizer/report.py` 重构：
- `render_shopee_ads_markdown`（L59-105）状态判断抽为 `_report_status`（L108-118，逐分支等价，删掉一个不可达 `else`）；建议小节替换为 `_recommendation_section_lines`（L121-155）。
- `_recommendation_section_lines`：current 时 `## 今日最新建议` 只渲染非 hold 可执行动作，无则输出「今天没有直接调整投放的建议。」（与 `web.py:552` 逐字一致）；存在 PRIMARY+HOLD 时追加 `## 保持不动：观察与核查（N）`（与 `web.py:556` 标题口径一致），其下复用 `_recommendation_lines` 渲染完整明细；非 current 时不渲染任何明细（旧数据不得当今日建议）。
- 删除「今日另有 N 个活动无需操作。」；`_recommendation_detail_lines`（L309-314）把 HOLD 动作行后缀改为「，不计作广告调整」（不再写「请人工修改」）。
- `_PLAN_FORMAT_NOTICE` / `_plan_items` / `_plan_texts` / `_codex_plan_lines` 加固：`execution_steps` 键缺失/`null`/非列表 → 返回格式异常提示而非抛 `TypeError`；`recovery_plan.remediation_requirements`/`branches` 为 `null` → 安全跳过；只保留文本项。

## 验证
需求依据：`docs/requirements/2026-09-16-shopee-local-ads-optimizer.md` 第 70/117/136/152 行。`tests/optimizer/test_report.py` 由 18 条增至 24 条；`.venv/bin/python -m pytest -q` → 550 passed in 36.76s；`.venv/bin/ruff check --no-cache .` → All checks passed。幂等重建日报（`--run-id codex-2026-09-16-d6264af449934d44ba964ca650602822`，走 `repository.py:1354-1364` 已发布幂等分支，不写库）：`## 保持不动：观察与核查（41）` 含 41 条明细与完整核查步骤，`保持并观察` 41 次、「不计作广告调整」42 次、「请人工修改」0 次、全文无「今日另有」「无需操作」；DB 未因重建日报写入（批次建议仍 41 条全 `hold`，`optimizer_recommendations` 总数 330，`optimizer_recommendation_feedback` 0）；`/`、`/campaigns`、`/reviews`、`/analysis?date=2026-09-16` 均 200。

## 适用范围与修订
`src/adwatch/optimizer/report.py`、`tests/optimizer/test_report.py`。未提交、未推送、未远程部署。遗留未决：需求第 137 行字面提到「网页及日报正确展示名称、日期、方案和反馈入口」，但日报是静态 Markdown，当前未写入本地反馈入口 `http://127.0.0.1:8765/`，文档中也无「日报必须含该链接」的明确约定，未擅自实现，待用户判断（若要做须从配置取端口而非硬编码 URL）。无替代条目。

## 教训
「保持不动」不等于「无需操作」——把 hold 统一折叠成一句「无需操作」会同时违背可解释性要求。渲染层过滤动作类型时，必须对齐需求文档中「哪类动作必须可见」的显式条款，并以需求行号作为验收依据。（AI 归纳，仅供参考）

## 关联
- 本地补充来源: [handoff](../../handoffs/shopee_codex_cutover_2026-09-16.md)（不随 Git 同步）
- 持久证据: 需求文档 `docs/requirements/2026-09-16-shopee-local-ads-optimizer.md` 第 70/117/136/152 行、§10；代码 `src/adwatch/optimizer/report.py` `_recommendation_section_lines` / `_recommendation_detail_lines` / `_codex_plan_lines`；批次 `codex-2026-09-16-d6264af449934d44ba964ca650602822`
- 必要证据摘要: report.py 单列「保持不动：观察与核查（41）」；550 passed；重建日报不写库（recommendations 330、feedback 0）；需求第 137 行反馈入口待用户判断
- 相关: 无