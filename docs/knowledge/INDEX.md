# Cross_Border_Electronic_Commerce 知识库索引

最后巡检: 2026-10-13（冷启动初始化 + 同日 code-document-curator 放下时复核）
条目统计: 问题 3 / 决策 2 / 变更日志累计 1

## 巡检状态汇总
| 状态   | 数量 | 判定规则              |
|--------|------|-----------------------|
| 正常   | 4    | 本次检查未发现需复查项，不代表重新验证全部结论 |
| 待复查 | 1    | 存在证据缺口、相关代码变化或超 90 天未核对等复查提示 |
| 孤立   | 2    | 无其他知识正文条目引用；索引入口不算入链，仅作提示 |

> 冷启动说明：5 条黄金样例的「审查」字段已于 2026-10-13 由 code-document-curator 完成放下时复核（逐条与来源 handoff 1:1 核对，均通过），由「待审查」转为「已审查」——「审查」仅表核对来源与表述，与状态互不混用，也不代表结论永久有效。另按「状态」维度，1 条因遗留未决列为「待复查」（见下方清单），与「审查」是两个独立轴。

## 问题模式库
| ID | 标题 | 标签 | 日期 | 状态 |
|----|------|------|------|------|
| Cross_Border_Electronic_Commerce-20261013-collect-failure-token | 采集失败日志只留 collection_incomplete，事后无法区分真因 | 可观测性,采集,日志 | 2026-09-20 | 已解决 |
| Cross_Border_Electronic_Commerce-20261013-shopee-report-hold-hidden | 日报把 hold 保持方案过滤掉，41 条保持明细全部不可见 | bug,report,需求符合性 | 2026-09-16 | 已解决 |
| Cross_Border_Electronic_Commerce-20261013-dashboard-stale-code-400 | 未重启的 dashboard 加载旧代码，保存活动档案被吞成 400「表单字段无效」 | bug,运维,缓存 | 2026-09-21 | 已解决 |

## 决策记录
| ID | 标题 | 日期 | 状态 | 替代入口 |
|----|------|------|------|----------|
| Cross_Border_Electronic_Commerce-20261013-rapid-boost-revert-hold | 撤回两条「关闭 Rapid Boost」建议，先补因果证据再谈执行 | 2026-09-21 | 已采纳 | — |
| Cross_Border_Electronic_Commerce-20261013-shop-gmv-max-numeric-stop | Shop GMV Max 数值动作终止投入，仅保留分类动作且不放宽有限上界门禁 | 2026-09-21 | 已采纳 | — |

## 每日变更（近 30 天）
| 日期 | 变更数 | 摘要 |
|------|--------|------|
| 2026-10-13 | 1 | [knowledge-base-build] 建立 Cross_Border 知识库冷启动骨架（3 模板 + 5 黄金样例 + INDEX），从近 30 天 handoff 提炼 3 issue + 2 decision |

## 待复查清单
- [ ] Cross_Border_Electronic_Commerce-20261013-shopee-report-hold-hidden：需求第 137 行「日报含本地反馈入口 URL」遗留未决，待用户判断

## 孤立条目
- Cross_Border_Electronic_Commerce-20261013-collect-failure-token（无其他知识正文条目引用）
- Cross_Border_Electronic_Commerce-20261013-shopee-report-hold-hidden

## 关联
- [项目文档入口](../README.md)