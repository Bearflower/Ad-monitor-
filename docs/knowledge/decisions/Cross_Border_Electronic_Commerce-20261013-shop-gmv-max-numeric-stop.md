---
id: Cross_Border_Electronic_Commerce-20261013-shop-gmv-max-numeric-stop
类型: decision
项目: Cross_Border_Electronic_Commerce
标题: Shop GMV Max 数值动作终止投入，仅保留分类动作且不放宽有限上界门禁
日期: 2026-09-21
标签: [shopee, 门禁设计]
任务标识: shopee_verified_controls_2026-09-21.md
状态: 已采纳
审查: 已审查
---

# Shop GMV Max 数值动作终止投入，仅保留分类动作且不放宽有限上界门禁

## 背景
Shop GMV Max 活动 ID 为合成 ID `27679338786521:shop_gmv_max:__SHOP_GMV_MAX__`，Shopee 侧无可提交的 `campaign_id`；其预算面板文案为 "Edit Budget **Unlimited** Set daily budget ฿"，无有限上界，无法构造 `range`。

## 备选方案
- A. 为 Shop GMV Max 放宽 `_has_numeric_constraint` 的有限上界要求，让数值动作通过门禁。
- B. 终止其数值动作投入，仅保留分类动作（`set_rapid_boost` / `set_bidding_mode`），不放宽门禁。

## 决策
采纳 B：Shop GMV Max 数值动作（预算 / 目标 ROAS）终止投入，仅保留分类动作，且**不为它放宽** `_has_numeric_constraint` 的有限上界要求。

## 理由
「加预算」在 Unlimited 语义下本就无意义，门禁拒绝是语义正确的；活动为合成 ID，Shopee 侧无真实 `campaign_id` 可提交。

## 后果
- 眼下收益：数值门禁语义保持一致（无有限上界即拒绝数值动作），不引入「为个别活动开口子」的特殊分支。
- 技术债：Shop GMV Max 数值动作长期不可用，预算需人工在后台处理；其分类动作依赖真实控件（Rapid Boost 开关）。

## 适用范围与修订
`src/adwatch/optimizer/controls.py` 的 `_has_numeric_constraint`（需 `min`+`max` 同时有限）。本决策至今有效、无替代条目。相关终局语义：140295224 的 Rapid Boost 控件经同页对照法核实确实不存在（其出价模式为 `GMV Max Auto Bidding`），`control_missing` 与 `control_dependent_settings_required` 的拒绝为正确且终局的语义，不再尝试绕过或补证据。

## 关联与证据
- 本地补充来源: [handoff](../../handoffs/shopee_verified_controls_2026-09-21.md)（不随 Git 同步）
- 持久证据: `src/adwatch/optimizer/controls.py` `_has_numeric_constraint`；需求文档 `docs/requirements/2026-09-16-shopee-local-ads-optimizer.md` 第 13 节；控件证据 `shopee-controls/v1`、`optimizer_control_evidence`
- 必要证据摘要: Shop GMV Max 面板为 "Unlimited" 无有限上界、为合成 ID 无真实 `campaign_id`；140295224 全量 DOM（约 2.2 MB）中 `boost` 零命中、成因为 Auto 模式；两者数值/分类动作门禁拒绝为正确终局语义
- 相关: [撤回两条关闭 Rapid Boost 建议](Cross_Border_Electronic_Commerce-20261013-rapid-boost-revert-hold.md)