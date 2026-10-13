---
id: Cross_Border_Electronic_Commerce-20261013-collect-failure-token
类型: issue
项目: Cross_Border_Electronic_Commerce
标题: 采集失败日志只留 collection_incomplete，事后无法区分真因
日期: 2026-09-20
标签: [可观测性, 采集, 日志]
任务标识: adwatch_collect_failure_note_2026-09-20.md
状态: 已解决
审查: 已审查
---

# 采集失败日志只留 collection_incomplete，事后无法区分真因

## 现象
2026-09-20 日报 `prepare-analysis` 返回 `status=failed`（退出码 2），run log 只留下 `{"stage":"backfill","error_type":"collection_incomplete"}`，看不到真因。

## 根因
采集失败写入 `optimizer_collection_days.note` 的判据只有裸 `page_read_failed`，无法区分页面超时 / 导航被跳转 / 紫鸟认证失败 / Bridge 不可用 / Shopee 会话失效 / 店铺不匹配 / 紫鸟 CLI 失败 / 契约错误 / IO 错误。

## 解决
- `src/adwatch/optimizer/ziniao.py`：新增 `COLLECTION_FAILURE_TOKENS`（10 令牌有序映射元组，顺序为 ziniao 子类在前、`ZiniaoCliError` 基类其后、内建异常最末）与 `COLLECTION_FAILURE_FALLBACK="unexpected_error"`；纯函数 `collection_failure_token(error)`（isinstance 遍历 + `try/except Exception` 回落兜底；不读 `str/repr/args/__dict__/__class__.__name__`，避免异常原文含 URL、Cookie、业务数据）。
- `src/adwatch/optimizer/collector.py`：采集失败分支改写 `note=f"page_read_failed:{collection_failure_token(error)}"`，末尾裸 `raise`；补 import。
- 10 个令牌：`navigation_dropped` / `page_timeout` / `bridge_unavailable` / `ziniao_auth_failed` / `login_expired` / `store_mismatch`(StoreNotFound/StorePlatformMismatch/StoreNameMismatch 三类共映射) / `ziniao_cli_failed` / `contract_error` / `io_error` / `unexpected_error`。

## 验证
本任务真实证据：`.venv/bin/ruff check src tests` → All checks passed；`.venv/bin/python -m pytest -q` → 770 passed；`tests/optimizer/test_collector.py` → 108 passed（新增 `RaisingPageReader`、`_collection_row` 复用既有 fake，6 组用例含 14+ 项令牌参数化、落库端到端、隐私断言、`repository=None`、success 不降级、病态 `__class__` 回归）。独立复验：病态 `__class__` 实例返回 `unexpected_error`（修复前会抛 RuntimeError 掩盖原异常且不落库）、裸 `ZiniaoCliError` → `ziniao_cli_failed`、`KeyError` → `unexpected_error`。

## 适用范围与修订
`src/adwatch/optimizer/{ziniao,collector}.py`、`tests/optimizer/test_collector.py`。本次改动未提交、未部署（无 commit SHA，可查 handoff）。已知边界（非缺陷）：令牌只覆盖「异常类型」维度，不含页号/阶段（r1 判定为过度设计）；历史裸 `page_read_failed` 行仍无法区分真因，不回填。无替代条目。

## 教训
事后可观测性要在「出错那一刻」就写入足够分类的最小信息，且只落安全化枚举、绝不落异常原文。单维度（异常类型）已够定位，叠加页号/阶段判定为过度设计。（AI 归纳，仅供参考）

## 关联
- 本地补充来源: [handoff](../../handoffs/adwatch_collect_failure_note_2026-09-20.md)（不随 Git 同步）
- 持久证据: 需求文档 `docs/requirements/2026-09-16-shopee-local-ads-optimizer.md` §12.6、§12.10（10 令牌对照表、隐私红线）；运行手册 `docs/operations/adwatch-macos-schedule.md`；代码 `src/adwatch/optimizer/ziniao.py` `COLLECTION_FAILURE_TOKENS` / `collection_failure_token`、`src/adwatch/optimizer/collector.py` 采集失败分支
- 必要证据摘要: 10 令牌映射 + `unexpected_error` 兜底；770 passed（test_collector 108 passed）；令牌不含页号/阶段、历史裸行不回填；本改动未提交
- 相关: 无