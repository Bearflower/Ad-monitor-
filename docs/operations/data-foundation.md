# 数据底座操作手册

## 运行目录

`ADWATCH_DATA_DIR` 指定所有运行数据的位置：

- `adwatch.sqlite3`：主数据库。
- `reports/`：数据质量报告。
- 后续阶段的下载报表、截图和日报也将放在该目录下。

运行目录、`.env`、SQLite 文件和 Python 缓存均已被 Git 忽略。

## 初始化与健康检查

```bash
source .venv/bin/activate
export ADWATCH_DATA_DIR="$PWD/var"
python -m adwatch init
python -m adwatch doctor
```

`init` 可重复执行，只应用尚未执行的数据库迁移。`doctor` 当前显示 SQLite 路径和紫鸟配置状态。

## 模拟采集

```bash
python -m adwatch collect --mode mock --date 2026-07-22
```

每个平台生成四条确定性记录。相同平台和日期每次生成相同内容，方便重放和自动化测试。质量报告明确标记为模拟数据。

## 幂等与重跑

每日指标的逻辑唯一键由以下字段组成：

1. 平台
2. 店铺
3. 广告账户
4. Campaign
5. SKU
6. 数据日期

再次采集相同键时更新指标，不插入重复行。每次采集仍会新增独立的 `collection_runs` 审计记录。

## 数据隔离

空标识、负数、未来日期和未知币种不会写入指标表，而会保存到 `quarantined_records`。对应原因保存在 `issues_json`，批次汇总保存在 `quality_checks`。

可用 SQLite 客户端或 DBeaver 查看：

```sql
SELECT run_id, raw_json, issues_json
FROM quarantined_records
ORDER BY id DESC;
```

## 备份

停止 Adwatch 相关进程后，复制 `adwatch.sqlite3` 到备份目录。当前阶段没有后台常驻写入进程；后续启用调度器后，应先停止调度服务再复制。

恢复时停止程序，用备份文件替换 `adwatch.sqlite3`，然后执行：

```bash
python -m adwatch init
```

该命令会补齐备份版本之后新增的迁移。

## 紫鸟真实适配器前置配置

```dotenv
ZINIAO_COMPANY=企业名称
ZINIAO_USERNAME=自动化成员用户名
ZINIAO_PASSWORD=自动化成员密码
ZINIAO_ENDPOINT=http://127.0.0.1:1886
```

敏感值不得写入仓库。缺失任一变量时，真实采集会明确失败，不会使用模拟数据代替。
