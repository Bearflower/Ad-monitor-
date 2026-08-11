# Adwatch 遗漏功能补全与现场激活设计

日期：2026-07-27  
状态：待用户复核  
目标：补齐完整实施计划中尚未达到验收标准的功能，同时确保未经真实页面
验证的广告写动作无法进入 Live。

## 1. 验收边界

系统采用两层完成状态：

1. `code_ready`：确定性逻辑、命令、数据模型、错误处理和自动化测试完成。
2. `field_activated`：依赖真实账号或外部服务的能力已经现场核验并记录证据。

`code_ready` 不自动推导 `field_activated`。广告动作只有同时满足代码就绪、
选择器配置已激活、有效审批、熔断关闭、全局 Live 开关和精确允许清单时，
才能执行真实提交。

## 2. 数据可信度

每日分析结果公开四个逐级状态：

- `platform_metrics`：平台核心指标已采集。
- `estimated_profit`：使用最小成本或默认零项得到估算利润。
- `verified_profit`：完整经营成本已经校验。
- `inventory_safe_strategy`：库存数据足以生成加预算建议。

缺少成本或库存只降低对应能力等级，不阻塞日报，也不触发数据质量熔断。
任何报告、看板和策略不得把缺失值展示为已验证的零。

## 3. 采集可靠性

紫鸟页面读取采用最多 3 次的有界重试。每次失败写入采集运行记录；
单平台失败不阻塞另一平台。最终失败状态必须出现在质量报告、日报和看板，
不得输出 `daily_run=ok`。

## 4. 策略补全

现有预算、ROAS 目标和暂停/恢复建议保留。新增商品复测池建议：

- 仅使用可分配预算的最多 20%；
- 缺少已验证利润或库存时不生成扩量/复测建议；
- 输出确定性比例、原因、时间窗口和审批等级；
- 只生成建议，不自动建立或删除 Campaign。

## 5. 报告、看板和备份

CLI 补齐：

```text
adwatch report daily --date YYYY-MM-DD
adwatch report weekly --end YYYY-MM-DD
adwatch report monthly --month YYYY-MM
adwatch backup create --output PATH
adwatch backup verify --path PATH
```

看板增加 7/14/30 天趋势、最近采集运行质量、审批状态和执行审计状态，
并保留平台、店铺、Campaign、SKU 筛选。

## 6. 紫鸟 Shadow/Live 页面动作

执行层拆分为：

- 平台适配器：TikTok 与 Shopee 独立。
- 动作适配器：预算、ROAS 目标、暂停、恢复独立。
- 选择器配置：按平台和动作保存版本及激活状态。

每个动作必须实现：

1. 读取当前值；
2. 校验与审批预期值一致；
3. Shadow 计算预期结果但不点击提交；
4. Live 填入目标值并提交；
5. 回读确认；
6. 保存提交前后截图；
7. 失败时恢复旧值并再次确认。

禁止对调用方暴露任意 JavaScript 写入接口。未激活选择器配置时，
Shadow 可以读取并报告 `pending_external`，Live 必须拒绝。

## 7. 现场激活记录

SQLite 保存每个 `platform/action` 的选择器版本、激活时间、操作者、
验证店铺和证据截图。上线检查逐项核对：

- 紫鸟 Bridge；
- TikTok/Shopee 有数据页面读取；
- 每个 Live 动作的选择器激活；
- 经营成本、SKU 映射、退款、库存和汇率；
- 飞书公网 HTTPS 回调；
- Shadow 连续对账；
- 回滚与熔断恢复演练；
- 官方 API OAuth（若启用）；
- Live 精确允许清单。

## 8. 测试与完成标准

所有修改使用 TDD。完成必须同时满足：

- 新测试先失败，再由最小实现变绿；
- 全量 pytest 通过；
- `ruff check --select E,F,I src tests` 通过；
- CLI 帮助中存在所有约定命令；
- 动态上线清单只剩真实数据、账号和现场验证事项；
- `ADWATCH_LIVE_WRITES=false`；
- 未激活选择器的 Live 测试证明在页面提交前被拒绝。

## 9. 非目标

- 不在没有真实 Campaign 的情况下猜测页面选择器。
- 不自动开启 Live。
- 不替用户创建或删除大型 Campaign。
- 不把可选官方 API 接入作为紫鸟 CLI 主线的阻塞条件。
