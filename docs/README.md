# Adwatch 文档索引

## 当前文档

- [项目入口](../README.md)：当前 Shopee 广告优化器的范围、安装和六个命令。
- [macOS 调度手册](operations/adwatch-macos-schedule.md)：唯一 09:00
  LaunchAgent 的查询、安装、卸载和排障步骤。

当前可执行实现仅位于 `src/adwatch/optimizer/`、`src/adwatch/cli.py` 和
`src/adwatch/config.py`。测试入口为 `tests/optimizer/`、`tests/test_cli.py` 和
`tests/test_config.py`。

## 历史资料

以下目录保留用于需求沿革、迁移审计和决策追溯，不是当前功能说明，也不能作为
现有命令、模块或部署方式的依据：

- `blueprint/`：早期系统蓝图与差距审计。
- `design/`：旧双平台、经营、执行、报表和基础设施设计。
- `plans/`：旧系统的实施计划与阶段记录。
- `workflow/`：早期审计过程文件。
- `deploy/`：历史部署材料；当前工具不上传或部署到服务器。
- `operations/data-foundation.md`：旧统一经营数据底座说明。

历史文件保持原样，以便解释既有 SQLite 表和迁移来源。它们不会重新启用已删除
的代码，也不会扩大当前 Shopee 只读优化器的范围。

## 当前边界

- 只采集 Shopee Ads。
- 不依赖订单、成本、利润、库存或退款数据。
- 不发送飞书或其他远程通知，不上传服务器。
- 不自动修改广告。
- 本地 Web 只监听 `127.0.0.1`。
- 安装 LaunchAgent 需要用户单独授权。
