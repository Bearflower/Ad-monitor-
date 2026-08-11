# Adwatch TikTok 采集开关设计

日期：2026-08-03  
状态：已批准并实施

## 一、背景与目标

2026-08-01 的历史补采中，TikTok 与 Shopee 页面采集均失败。用户决定先屏蔽
TikTok 账号采集，保留 Shopee、汇率、订单同步、分析、本地报告和定时任务。

本次目标是增加可逆的生产配置开关，而不是删除 TikTok 店铺配置、历史数据或
账号授权。关闭后不得再访问 TikTok 页面，也不得因为 TikTok 未采集而把每日
运行标记为部分失败或发送 TikTok 故障通知。

## 二、方案选择

采用环境配置开关：

```dotenv
ADWATCH_TIKTOK_COLLECTION_ENABLED=false
```

默认值为 `true`，保证未配置环境与现有行为兼容。实施时将当前生产 `.env`
设置为 `false`。重新启用时只需改回 `true`，无需恢复店铺 ID 或历史数据。

未采用以下方案：

- 清空 `ZINIAO_TIKTOK_STORE_ID`：会把“主动停用”误报为配置缺失，并影响
  Bridge 就绪门禁。
- 在采集逻辑中硬编码跳过：无法通过配置恢复，违反项目禁止硬编码规则。

## 三、配置与组件边界

`Settings` 新增布尔字段 `tiktok_collection_enabled`，从
`ADWATCH_TIKTOK_COLLECTION_ENABLED` 解析，接受项目现有的
`1/true/yes` 真值口径。

配置影响以下边界：

1. `run daily` 与 `run scheduled-daily`：关闭时只构造 Shopee 采集器。
2. `collect --mode ziniao`：关闭时只运行 Shopee，保证手工入口与定时入口一致。
3. Bridge 配置门禁：关闭时不要求 TikTok 店铺 ID，但仍要求 Shopee 店铺 ID。
4. 上线清单：关闭时不展示 `tiktok_campaign_validation` 阻塞项。

开关不影响：

- 数据库中已有 TikTok 指标、运行记录和历史报告。
- Dashboard 对历史 TikTok 数据的只读展示。
- Shopee 店铺配置、汇率解析、订单与 SKU 成本、库存、分析和飞书总开关。

## 四、运行数据流

关闭 TikTok 后，每日流程为：

```text
执行日汇率
→ 紫鸟 Bridge 与 Shopee 店铺配置检查
→ Shopee 页面采集
→ 质量报告
→ 订单同步
→ 利润与策略分析
→ 本地日报
→ 按调用参数决定是否发送飞书
```

质量报告只记录实际启用的平台。TikTok 不创建采集运行记录、不写故障日志，
也不参与 `partial` 判断。Shopee 失败仍按现有规则记录独立故障并返回 partial。

## 五、0801 历史补采

代码与生产配置生效后执行：

```bash
.venv/bin/adwatch run daily --mode ziniao --date 2026-08-01 --no-notify
```

该命令必须：

- 不发送普通日报或故障卡片。
- 使用现有执行日汇率回退逻辑。
- 只访问 Shopee，不访问 TikTok。
- 成功时写入 0801 Shopee 指标、质量报告和本地日报，并刷新 Dashboard 最新日。
- Shopee 仍失败时保留真实 partial 结果，不用旧值或模拟值伪造成功。

## 六、测试与验收

采用 TDD，至少覆盖：

1. 配置缺省时 TikTok 保持启用。
2. `.env` 关闭开关时配置解析为禁用。
3. TikTok 禁用且仅配置 Shopee 店铺时，紫鸟配置门禁通过。
4. 每日采集只构造 Shopee，TikTok 不产生失败项。
5. 手工 `collect` 入口同样只运行 Shopee。
6. 上线清单不再显示 TikTok 现场验证阻塞项。
7. TikTok 重新启用后现有双平台行为不变。

生产验收证据包括：目标测试、全量回归、Ruff、compileall、0801 数据库指标、
质量报告、日报、飞书 suppressed 状态和 Dashboard 页面。未经 `/git` 指令不
提交或推送代码。
