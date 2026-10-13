---
id: Cross_Border_Electronic_Commerce-20261013-rapid-boost-revert-hold
类型: decision
项目: Cross_Border_Electronic_Commerce
标题: 撤回两条「关闭 Rapid Boost」建议，先补因果证据再谈执行
日期: 2026-09-21
标签: [shopee, 投放决策]
任务标识: shopee_verified_controls_2026-09-21.md
状态: 已采纳
审查: 已审查
---

# 撤回两条「关闭 Rapid Boost」建议，先补因果证据再谈执行

## 背景
2026-09-21 v3 批次曾产出两条 `set_rapid_boost`（true→false）建议——活动 133720879 与 Shop GMV Max，见批次 `codex-2026-09-21-a1a00a603e574a89af6e835dfbd78d53`。用户质疑关闭 Rapid Boost 的不可逆性：「如果策略关闭了，那就已经不再检测了，不会再有优化调整的可能了」。

## 备选方案
- A. 按原建议执行关闭 Rapid Boost。
- B. 两条 Rapid Boost 建议全部撤回为 `hold`，先补因果证据再谈执行。

## 决策
采纳 B：两条 Rapid Boost 建议全部撤回为 `hold`，证据源选定后台 **Operation History（变更记录）**，先补因果证据再谈执行。

## 理由
关闭 Rapid Boost 不可逆，且缺「开启期 vs 关闭期」对照。只读取证（未执行任何写操作）显示：两活动近 30 天均无 Rapid Boost / 出价方式 / 目标 ROAS 的变更记录，Rapid Boost 全程 Enabled，因果证据缺失（缺失项 = `parameter_change_log`）。

## 后果
- 眼下收益：避免在因果证据不足时执行不可逆动作。
- 技术债 / 历史降级：被取代的 `codex-2026-09-21-a1a00a603e574a89af6e835dfbd78d53`（2 条 `set_rapid_boost` + 1 条 hold）仍留在库中作历史，不再是当前建议来源；重发批次 `codex-2026-09-21-a490a3e15d2441d495010d150e684bbd` 三条建议全部 `hold`；失效的中间产物 `var/analysis/outbox/codex-2026-09-21-f6a2d394246242ee8702c2d749ff5227.json` 已删除（run_id 未在库中注册而发布失败）。

## 适用范围与修订
涉及活动 133720879、Shop GMV Max（合成 ID `27679338786521:shop_gmv_max:__SHOP_GMV_MAX__`）与 140295224（沿用原 hold 结论）。本决策至今有效、无替代条目；被本决策撤回的是两条关闭 Rapid Boost 的建议本身。

## 关联与证据
- 本地补充来源: [handoff](../../handoffs/shopee_verified_controls_2026-09-21.md)（不随 Git 同步）
- 持久证据: 需求文档 `docs/requirements/2026-09-16-shopee-local-ads-optimizer.md` 第 13 节；重发批次 `codex-2026-09-21-a490a3e15d2441d495010d150e684bbd`；Operation History 只读取证记录（`/Users/yl/.codex/automations/shopee/evidence/2026-09-21/operation-history.md`，本机路径）
- 必要证据摘要: 两活动近 30 天无 Rapid Boost / 出价方式 / 目标 ROAS 变更记录，缺失项 `parameter_change_log`；重发批次三条全 hold；`set_rapid_boost` true→false 建议已撤回
- 相关: [未重启的 dashboard 加载旧代码](../issues/Cross_Border_Electronic_Commerce-20261013-dashboard-stale-code-400.md)；[Shop GMV Max 数值动作终止投入](Cross_Border_Electronic_Commerce-20261013-shop-gmv-max-numeric-stop.md)