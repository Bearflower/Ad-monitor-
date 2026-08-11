# TikTok 采集开关实施计划

> **执行要求：** 使用 `executing-plans` 或 `subagent-driven-development` 逐项实施；所有生产代码遵循 RED→GREEN。未经用户 `/git` 指令，不执行计划中的 Git 提交或推送。

**目标：** 通过生产配置关闭 TikTok 采集，同时保持 Shopee、汇率、订单、分析、报告和定时任务正常运行，并完成 2026-08-01 Shopee 无通知补采。

**架构：** 配置层提供唯一的平台启用口径；每日流水线和手工采集入口共同读取该口径，避免行为漂移。上线清单显式排除已停用平台的现场门禁，历史数据保持只读可见。

**技术栈：** Python 3.12、dataclass、argparse、pytest、Ruff、SQLite、ziniao-cli、macOS LaunchAgent。

---

## 文件边界

- 修改 `src/adwatch/config.py`：解析 TikTok 开关并提供启用平台列表及 CLI 门禁。
- 修改 `src/adwatch/operations/daily_run.py`：每日流水线只构造启用平台采集器。
- 修改 `src/adwatch/cli.py`：手工真实采集与上线清单尊重开关。
- 修改 `src/adwatch/operations/launch_checklist.py`：支持显式排除停用平台门禁。
- 修改 `.env.example`、`.env`：记录开关并在当前生产环境关闭 TikTok。
- 修改 `README.md`、`docs/workflow/progress.md`：记录使用方式和现场结果。
- 修改 `tests/test_config.py`、`tests/operations/test_daily_run.py`、
  `tests/operations/test_launch_checklist.py`、`tests/test_cli.py`：覆盖配置、双入口和清单。

### 任务 1：配置层开关与门禁

**文件：**

- 修改：`src/adwatch/config.py`
- 测试：`tests/test_config.py`
- 修改：`.env.example`

- [x] **步骤 1：写配置 RED 用例**

在 `tests/test_config.py` 增加：

```python
def test_tiktok_collection_is_enabled_by_default(monkeypatch):
    monkeypatch.delenv("ADWATCH_TIKTOK_COLLECTION_ENABLED", raising=False)
    settings = Settings.from_env()
    assert settings.tiktok_collection_enabled is True
    assert settings.enabled_ziniao_platforms == ("tiktok", "shopee")


def test_disabled_tiktok_only_requires_shopee_store_id(monkeypatch):
    monkeypatch.setenv("ADWATCH_TIKTOK_COLLECTION_ENABLED", "false")
    monkeypatch.delenv("ZINIAO_TIKTOK_STORE_ID", raising=False)
    monkeypatch.setenv("ZINIAO_SHOPEE_STORE_ID", "222")
    settings = Settings.from_env()
    assert settings.tiktok_collection_enabled is False
    assert settings.enabled_ziniao_platforms == ("shopee",)
    assert settings.ziniao_cli_ready is True
```

- [x] **步骤 2：运行 RED**

运行：

```bash
.venv/bin/python -m pytest \
  tests/test_config.py::test_tiktok_collection_is_enabled_by_default \
  tests/test_config.py::test_disabled_tiktok_only_requires_shopee_store_id -q
```

预期：因字段或属性不存在而失败。

- [x] **步骤 3：实现最小配置代码**

在 `Settings` 增加：

```python
tiktok_collection_enabled: bool = True
```

在 `from_env()` 构造中增加：

```python
tiktok_collection_enabled=value(
    "ADWATCH_TIKTOK_COLLECTION_ENABLED", "true"
).lower() in {"1", "true", "yes"},
```

增加统一平台属性并修正门禁：

```python
@property
def enabled_ziniao_platforms(self) -> tuple[str, ...]:
    if self.tiktok_collection_enabled:
        return ("tiktok", "shopee")
    return ("shopee",)

@property
def ziniao_cli_ready(self) -> bool:
    return bool(self.ziniao_shopee_store_id) and (
        not self.tiktok_collection_enabled
        or bool(self.ziniao_tiktok_store_id)
    )
```

在 `.env.example` 增加：

```dotenv
ADWATCH_TIKTOK_COLLECTION_ENABLED=true
```

- [x] **步骤 4：运行 GREEN 与配置回归**

运行：

```bash
.venv/bin/python -m pytest tests/test_config.py -q
.venv/bin/ruff check src/adwatch/config.py tests/test_config.py
```

预期：全部通过。

### 任务 2：每日流水线只采启用平台

**文件：**

- 修改：`src/adwatch/operations/daily_run.py`
- 测试：`tests/operations/test_daily_run.py`

- [x] **步骤 1：写每日采集 RED 用例**

新增测试，使用记录构造参数的假 `ZiniaoCollector`：

```python
def test_production_collection_skips_disabled_tiktok(tmp_path, monkeypatch):
    platforms = []

    class EmptyCollector:
        source = "ziniao"

        def __init__(self, settings, platform):
            self.platform = platform
            platforms.append(platform.value)

        def collect(self, data_date):
            return []

    monkeypatch.setattr(
        "adwatch.operations.daily_run.ZiniaoCollector", EmptyCollector
    )
    settings = Settings(
        data_dir=tmp_path,
        ziniao_shopee_store_id="222",
        tiktok_collection_enabled=False,
    )
    database = Database(settings.database_path)
    database.migrate()

    outcome = ProductionDailyRunDependencies(settings, database).collect(
        date(2026, 8, 1), "ziniao"
    )

    assert platforms == ["shopee"]
    assert outcome.failures == ()
```

- [x] **步骤 2：运行 RED**

运行：

```bash
.venv/bin/python -m pytest \
  tests/operations/test_daily_run.py::test_production_collection_skips_disabled_tiktok -q
```

预期：实际构造 `tiktok` 与 `shopee`，断言失败。

- [x] **步骤 3：实现最小平台过滤**

把每日采集的平台选择改为：

```python
platforms = (
    (Platform.TIKTOK, Platform.SHOPEE)
    if mode == "mock"
    else tuple(
        Platform(value)
        for value in self.settings.enabled_ziniao_platforms
    )
)
for platform in platforms:
    collector = (
        collector_type(platform)
        if mode == "mock"
        else collector_type(self.settings, platform)
    )
    try:
        summaries.append(runner.run(collector, report_date))
    except Exception as error:  # noqa: BLE001 - 每个平台独立隔离
        failures.append(
            CollectionFailure(
                platform=platform.value,
                error_type=type(error).__name__,
                message=str(error),
            )
        )
```

- [x] **步骤 4：运行 GREEN 与每日流水线回归**

运行：

```bash
.venv/bin/python -m pytest tests/operations/test_daily_run.py \
  tests/test_daily_run_cli.py -q
.venv/bin/ruff check src/adwatch/operations/daily_run.py \
  tests/operations/test_daily_run.py
```

预期：全部通过。

### 任务 3：手工采集入口与错误提示

**文件：**

- 修改：`src/adwatch/cli.py`
- 测试：`tests/test_cli.py`

- [x] **步骤 1：写 CLI RED 用例**

新增真实模式测试，替换 `ZiniaoCollector` 和 `PipelineRunner.run`，记录平台：

```python
def test_collect_ziniao_skips_disabled_tiktok(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path,
        ziniao_shopee_store_id="222",
        tiktok_collection_enabled=False,
    )
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    platforms = []

    class Collector:
        def __init__(self, settings, platform):
            self.platform = platform
            platforms.append(platform.value)

    monkeypatch.setattr(adwatch.cli, "ZiniaoCollector", Collector)
    monkeypatch.setattr(
        adwatch.cli.PipelineRunner,
        "run",
        lambda self, collector, data_date: type(
            "Summary",
            (),
            {
                "platform": collector.platform.value,
                "received": 0,
                "accepted": 0,
                "quarantined": 0,
                "run_id": "test",
                "source": "ziniao",
            },
        )(),
    )

    assert main(["collect", "--mode", "ziniao", "--date", "2026-08-01"]) == 0
    assert platforms == ["shopee"]
```

- [x] **步骤 2：运行 RED**

运行：

```bash
.venv/bin/python -m pytest \
  tests/test_cli.py::test_collect_ziniao_skips_disabled_tiktok -q
```

预期：平台列表包含 TikTok，断言失败。

- [x] **步骤 3：实现 CLI 平台过滤**

真实模式采集器改为：

```python
collectors = [
    ZiniaoCollector(settings, Platform(value))
    for value in settings.enabled_ziniao_platforms
]
```

配置缺失提示改为只说明“已启用平台的店铺 ID 未配置”，避免关闭 TikTok 后仍
要求 `ZINIAO_TIKTOK_STORE_ID`。

- [x] **步骤 4：运行 GREEN 与 CLI 回归**

运行：

```bash
.venv/bin/python -m pytest tests/test_cli.py -q
.venv/bin/ruff check src/adwatch/cli.py tests/test_cli.py
```

预期：全部通过。

### 任务 4：上线清单排除停用平台门禁

**文件：**

- 修改：`src/adwatch/operations/launch_checklist.py`
- 修改：`src/adwatch/cli.py`
- 测试：`tests/operations/test_launch_checklist.py`

- [x] **步骤 1：写清单 RED 用例**

```python
def test_launch_checklist_excludes_disabled_platform_items():
    items = build_launch_checklist(
        LaunchReadiness(),
        excluded_codes=frozenset({"tiktok_campaign_validation"}),
    )
    codes = {item.code for item in items}
    assert "tiktok_campaign_validation" not in codes
    assert "shopee_campaign_validation" in codes
```

- [x] **步骤 2：运行 RED**

运行：

```bash
.venv/bin/python -m pytest \
  tests/operations/test_launch_checklist.py::test_launch_checklist_excludes_disabled_platform_items -q
```

预期：`build_launch_checklist` 不接受 `excluded_codes`。

- [x] **步骤 3：实现显式排除并接入 CLI**

函数签名与循环增加：

```python
def build_launch_checklist(
    readiness: LaunchReadiness,
    *,
    excluded_codes: frozenset[str] = frozenset(),
) -> tuple[LaunchItem, ...]:
    items = []
    for code, description in DESCRIPTIONS.items():
        if code in excluded_codes:
            continue
        if not getattr(readiness, code):
            items.append(
                LaunchItem(
                    code,
                    description,
                    optional=code == "platform_api_oauth",
                )
            )
    return tuple(items)
```

CLI 调用增加：

```python
excluded_codes = (
    frozenset()
    if settings.tiktok_collection_enabled
    else frozenset({"tiktok_campaign_validation"})
)
items = build_launch_checklist(
    readiness,
    excluded_codes=excluded_codes,
)
```

- [x] **步骤 4：运行 GREEN 与清单回归**

运行：

```bash
.venv/bin/python -m pytest tests/operations/test_launch_checklist.py \
  tests/test_cli.py -q
```

预期：全部通过。

### 任务 5：生产配置、文档与静态检查

**文件：**

- 修改：`.env`
- 修改：`README.md`
- 修改：`docs/workflow/progress.md`

- [x] **步骤 1：关闭当前生产 TikTok 采集**

在 `.env` 写入：

```dotenv
ADWATCH_TIKTOK_COLLECTION_ENABLED=false
```

不得删除或覆盖现有店铺 ID、Key、Webhook 或其他配置。

- [x] **步骤 2：更新 README**

在真实采集说明中记录：默认启用 TikTok；临时停用时设置上述开关；停用不会
删除历史数据，手工与定时任务都只采 Shopee。

- [x] **步骤 3：运行静态检查**

```bash
.venv/bin/ruff check src/adwatch/config.py \
  src/adwatch/operations/daily_run.py \
  src/adwatch/operations/launch_checklist.py \
  src/adwatch/cli.py \
  tests/test_config.py tests/operations/test_daily_run.py \
  tests/operations/test_launch_checklist.py tests/test_cli.py
.venv/bin/python -m compileall -q src/adwatch
git diff --check
```

预期：全部退出 0。

### 任务 6：全量回归、现场配置与 0801 补采

**文件：**

- 生产数据：`var/adwatch.sqlite3`
- 运行工件：`var/reports/quality-2026-08-01.json`
- 运行工件：`var/reports/daily-2026-08-01.md`

- [x] **步骤 1：运行全量测试**

```bash
.venv/bin/python -m pytest -q
```

预期：本任务新增回归全部通过；如仍有既有 Dashboard 基线失败，必须单独列出
并确认与本任务无关。

- [x] **步骤 2：检查生产配置生效**

```bash
.venv/bin/python -c "from adwatch.config import Settings; s=Settings.from_env(); print(s.tiktok_collection_enabled, s.enabled_ziniao_platforms, s.ziniao_cli_ready)"
.venv/bin/adwatch launch-checklist --format markdown
```

预期：输出 `False ('shopee',) True`，上线清单不含
`tiktok_campaign_validation`。

- [x] **步骤 3：检查 Bridge 后无通知补采 0801**

```bash
ziniao-cli doctor
.venv/bin/adwatch run daily --mode ziniao --date 2026-08-01 --no-notify
```

预期：输出中没有 TikTok 采集失败；Shopee 成功时 `daily_run=ok`、
`delivery=suppressed`。Shopee 失败时按真实结果继续诊断，不宣称补采成功。

- [x] **步骤 4：核对数据库与工件**

查询 `daily_ad_metrics`、`collection_runs` 和两份报告，确认最新 0801 新运行只
包含 Shopee；`pending_sku_costs()` 仍为 0 是“全部有效订单 SKU 已有成本”的
正确业务结果。

- [x] **步骤 5：刷新 Dashboard**

重启本地 Dashboard 或刷新当前页面，验证 0801 Shopee 指标、质量状态和
“SKU 与库存”页面待补成本口径。

## 收尾审查

- 对照 `docs/design/2026-08-03-tiktok-collection-toggle-design.md` 逐项检查。
- 确认未删除 TikTok 历史数据、店铺 ID 或账号授权。
- 确认没有新增硬编码业务阈值。
- 更新 `docs/workflow/progress.md`，记录真实补采结果和仍存在的阻塞。
- 未收到 `/git` 时只保留工作区改动，不提交、不推送。
