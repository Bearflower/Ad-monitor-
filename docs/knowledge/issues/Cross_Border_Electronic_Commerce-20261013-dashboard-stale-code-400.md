---
id: Cross_Border_Electronic_Commerce-20261013-dashboard-stale-code-400
类型: issue
项目: Cross_Border_Electronic_Commerce
标题: 未重启的 dashboard 加载旧代码，保存活动档案被吞成 400「表单字段无效」
日期: 2026-09-21
标签: [bug, 运维, 缓存]
任务标识: shopee_verified_controls_2026-09-21.md
状态: 已解决
审查: 已审查
---

# 未重启的 dashboard 加载旧代码，保存活动档案被吞成 400「表单字段无效」

## 现象
2026-09-21 用户确认放开活动 133720879 的分类动作后，经 `/campaigns` 表单保存其档案（追加 `set_rapid_boost`）返回 400，首次保存失败。

## 根因
本地 dashboard 进程原先在 2026-09-18 启动，加载的是**不含分类动作的旧代码**。保存时 `Action("set_rapid_boost")` 抛 `ValueError`，被 `_save_profile` 的 `except` 吞成「表单字段无效」。Python 不支持热加载。

## 解决
重启本地 dashboard 进程后重新经 `/campaigns` 表单保存成功；133720879 档案升到 v3（`allowed_actions` 追加 `set_rapid_boost`，`minimum_roas=4.8`、`status=formal` 不变），经表单保存而非直接写库。

## 验证
重启 dashboard 后保存成功；据此产出首个**非 hold** 的真实 v3 批次 `codex-2026-09-21-128251f015724e879696402ac5fd059b`（`schema_version=codex-analysis/v3`、`status=published`、1 条 `set_rapid_boost`（true→false）+ 2 条 hold、0 条 review、`finished_at=2026-09-21T03:17:19Z`）；日报与网页今日行动已同步为该批次。

## 适用范围与修订
本地 dashboard 进程（`src/adwatch/optimizer/web.py`）。该经验适用于所有本地长驻 Python 服务：改源码后必须重启进程。无替代条目。

## 教训
「改了源码、页面能打开」不等于「新代码已生效」——长驻进程需先重启再验证行为；异常被宽泛 `except` 吞成通用文案（「表单字段无效」）会掩盖真实根因，排障时应先尝试最小复现（如直接构造动作对象）以暴露底层 `ValueError`。（AI 归纳，仅供参考）

## 关联
- 本地补充来源: [handoff](../../handoffs/shopee_verified_controls_2026-09-21.md)（不随 Git 同步）
- 持久证据: `src/adwatch/optimizer/web.py` `_save_profile`；批次 `codex-2026-09-21-128251f015724e879696402ac5fd059b`；需求文档 `docs/requirements/2026-09-16-shopee-local-ads-optimizer.md` 第 13 节
- 必要证据摘要: 旧进程 2026-09-18 启动加载无分类动作旧代码；重启后保存成功并产出 1 条 `set_rapid_boost` 的真实 v3 批次
- 相关: 无