# Adwatch LaunchAgent Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前仓库建设为唯一生产源，提供可重复管理、登录后可补偿、每天北京时间 09:00 执行且具备汇率、Bridge、故障日志和飞书故障闭环的 macOS LaunchAgent。

**Architecture:** 新增独立的调度管理、运行状态和每日编排模块，CLI 只负责参数解析和调用。LaunchAgent 通过 `RunAtLoad` 与 09:00 日历事件调用到期判断包装器；每日编排按“执行日汇率→Bridge→采集→分析→报告”执行，并把来源、状态和故障写入当前仓库 `var/`。

**Tech Stack:** Python 3.11+、SQLite、macOS launchd/launchctl、ECB XML、飞书 Webhook、pytest、Ruff。

---

## 实施状态（2026-07-30）

- Task 1–8：已完成实现、TDD 和目标静态检查。
- Task 9：文档、目标 Ruff、编译和本任务回归已完成；全量测试为
  275 passed、2 个开发前既有 Dashboard 失败。
- Task 10 步骤 1–5：已完成安装前快照、唯一代码源安装、旧引用清理、
  2026-07-29 禁通知补跑和当前登录会话验证。
- Task 10 步骤 6–7：待用户明确确认后执行重启/重新登录验收，并等待下一次
  真实 09:00 持久化验收。

---

## 文件结构

- Create: `src/adwatch/operations/run_state.py` — 原子状态、运行锁、故障 JSONL 和通知去重。
- Create: `src/adwatch/operations/scheduler.py` — plist 渲染、LaunchAgent 安装、状态、修复和卸载。
- Create: `src/adwatch/operations/daily_run.py` — 汇率、Bridge、采集、分析、报告和故障通知的单次编排。
- Create: `src/adwatch/operations/scheduled_daily.py` — 09:00 到期判断、登录补跑和幂等控制。
- Modify: `src/adwatch/config.py` — 调度、时区、Bridge 和汇率回退配置。
- Modify: `src/adwatch/integrations/exchange_rates.py` — 保留真实公布日期并解析执行日汇率。
- Modify: `src/adwatch/storage/migrations.py` — 汇率来源审计字段迁移。
- Modify: `src/adwatch/reporting/delivery.py` — 飞书故障卡片。
- Modify: `src/adwatch/cli.py` — 新命令、`--no-notify` 和薄适配。
- Modify: `.env.example`、`README.md`、`docs/README.md` — 配置和运维说明。
- Create: `docs/operations/adwatch-macos-schedule.md` — 安装、修复、补跑、重启和次日验收手册。
- Create/Modify: 对应 `tests/` 文件 — 每项生产行为先写失败测试。

未经用户输入 `/git`，所有“提交”步骤改为精确路径的 `git diff` 检查，不执行
`git add`、`git commit` 或 `git push`。

### Task 1：集中配置调度参数

**Files:**
- Modify: `src/adwatch/config.py`
- Modify: `tests/test_config.py`
- Modify: `.env.example`

- [ ] **Step 1: 写配置失败测试**

```python
def test_schedule_settings_are_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("ADWATCH_DAILY_RUN_TIME", "09:00")
    monkeypatch.setenv("ADWATCH_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setenv("ADWATCH_BRIDGE_RETRIES", "4")
    monkeypatch.setenv("ADWATCH_BRIDGE_RETRY_DELAY_SECONDS", "2")
    monkeypatch.setenv("ADWATCH_EXCHANGE_RATE_MAX_STALENESS_DAYS", "6")

    settings = Settings.from_env()

    assert settings.daily_run_hour == 9
    assert settings.daily_run_minute == 0
    assert settings.timezone == "Asia/Shanghai"
    assert settings.bridge_retries == 4
    assert settings.bridge_retry_delay_seconds == 2
    assert settings.exchange_rate_max_staleness_days == 6
```

- [ ] **Step 2: 验证测试按预期失败**

Run:
`PYTHONPATH=src .venv/bin/python -m pytest tests/test_config.py::test_schedule_settings_are_loaded_from_environment -q`

Expected: FAIL，提示 `Settings` 缺少 `timezone`、`bridge_retries` 或对应字段。

- [ ] **Step 3: 实现集中配置**

在 `Settings` 增加以下字段，并在 `from_env()` 使用现有 `positive_int()` 读取：

```python
timezone: str = "Asia/Shanghai"
bridge_retries: int = 3
bridge_retry_delay_seconds: int = 10
exchange_rate_max_staleness_days: int = 7
```

使用 `zoneinfo.ZoneInfo(timezone)` 验证时区名称；保持业务流程只读取配置对象，
不在调度、Bridge 或汇率模块重复声明默认值。

- [ ] **Step 4: 更新 `.env.example`**

```dotenv
ADWATCH_DAILY_RUN_TIME=09:00
ADWATCH_TIMEZONE=Asia/Shanghai
ADWATCH_BRIDGE_RETRIES=3
ADWATCH_BRIDGE_RETRY_DELAY_SECONDS=10
ADWATCH_EXCHANGE_RATE_MAX_STALENESS_DAYS=7
```

- [ ] **Step 5: 验证并检查精确 diff**

Run:
`PYTHONPATH=src .venv/bin/python -m pytest tests/test_config.py -q`

Expected: PASS，无配置警告。

Run:
`git diff -- src/adwatch/config.py tests/test_config.py .env.example`

Expected: 只包含本任务配置字段、验证和示例值。

### Task 2：持久化汇率真实来源

**Files:**
- Modify: `src/adwatch/storage/migrations.py`
- Create: `tests/storage/test_exchange_rate_resolution_migration.py`

- [ ] **Step 1: 写迁移失败测试**

```python
def test_exchange_rates_store_resolution_provenance(tmp_path):
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()

    with database.connect() as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(exchange_rates)")
        }

    assert {
        "execution_date",
        "source_date",
        "resolution_status",
        "resolved_at",
    } <= columns
```

- [ ] **Step 2: 验证迁移测试失败**

Run:
`PYTHONPATH=src .venv/bin/python -m pytest tests/storage/test_exchange_rate_resolution_migration.py -q`

Expected: FAIL，缺少四个来源审计字段。

- [ ] **Step 3: 添加 migration 19**

```python
(
    19,
    """
    ALTER TABLE exchange_rates ADD COLUMN execution_date TEXT;
    ALTER TABLE exchange_rates ADD COLUMN source_date TEXT;
    ALTER TABLE exchange_rates ADD COLUMN resolution_status TEXT;
    ALTER TABLE exchange_rates ADD COLUMN resolved_at TEXT;
    """,
),
```

保持四列可空，使历史数据和旧导入流程继续工作。

- [ ] **Step 4: 验证迁移幂等和全量存储测试**

Run:
`PYTHONPATH=src .venv/bin/python -m pytest tests/storage -q`

Expected: PASS，重复 `database.migrate()` 不报错。

### Task 3：按执行日解析并应用汇率

**Files:**
- Modify: `src/adwatch/integrations/exchange_rates.py`
- Modify: `tests/integrations/test_contracts.py`
- Modify: `tests/integrations/test_exchange_rate_ensure.py`

- [ ] **Step 1: 写真实公布日期契约失败测试**

```python
def test_ecb_published_rates_preserve_actual_dates():
    source = EcbExchangeRateSource(fetcher=lambda _: ECB_FIXTURE)

    rates = source.fetch_published(
        "THB", date(2026, 7, 24), date(2026, 7, 27)
    )

    assert set(rates) == {date(2026, 7, 24), date(2026, 7, 27)}
```

测试 fixture 复用现有 7 月 24 日和 7 月 27 日 ECB XML，不把周末合成为公布日。

- [ ] **Step 2: 写执行日、昨日和最近交易日失败测试**

```python
def test_scheduled_rate_uses_execution_day_and_applies_to_report_day(tmp_path):
    result = resolve_scheduled_exchange_rate(
        _database(tmp_path),
        PublishedSource({date(2026, 7, 30): Decimal("0.201")}),
        currency="THB",
        execution_date=date(2026, 7, 30),
        report_date=date(2026, 7, 29),
        max_staleness_days=7,
    )
    assert result.data_date == date(2026, 7, 29)
    assert result.source_date == date(2026, 7, 30)
    assert result.status == "execution_day"


def test_scheduled_rate_uses_previous_day_when_execution_day_missing(tmp_path):
    result = resolve_scheduled_exchange_rate(
        _database(tmp_path),
        PublishedSource({date(2026, 7, 29): Decimal("0.202")}),
        currency="THB",
        execution_date=date(2026, 7, 30),
        report_date=date(2026, 7, 29),
        max_staleness_days=7,
    )
    assert result.source_date == date(2026, 7, 29)
    assert result.status == "previous_day"
```

另写一个 7 天内最近交易日通过测试和一个 8 天前拒绝测试。
测试文件同时定义确定性辅助对象：

```python
@dataclass(frozen=True)
class PublishedSource:
    values: dict[date, Decimal]

    def fetch_published(self, currency, start, end):
        return {
            day: value
            for day, value in self.values.items()
            if start <= day <= end
        }


def _database(tmp_path):
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()
    return database
```

- [ ] **Step 3: 运行失败测试**

Run:
`PYTHONPATH=src .venv/bin/python -m pytest tests/integrations/test_contracts.py tests/integrations/test_exchange_rate_ensure.py -q`

Expected: FAIL，缺少 `fetch_published()` 和
`resolve_scheduled_exchange_rate()`。

- [ ] **Step 4: 实现汇率接口和解析结果**

```python
@dataclass(frozen=True)
class ExchangeRateResolution:
    currency: str
    data_date: date
    rate: Decimal
    status: str
    source_date: date
    execution_date: date | None = None


class PublishedExchangeRateSource(Protocol):
    def fetch_published(
        self, currency: str, start: date, end: date
    ) -> dict[date, Decimal]:
        raise NotImplementedError


def resolve_scheduled_exchange_rate(
    database: Database,
    source: PublishedExchangeRateSource,
    *,
    currency: str,
    execution_date: date,
    report_date: date,
    max_staleness_days: int,
) -> ExchangeRateResolution:
    start = execution_date - timedelta(days=max_staleness_days)
    try:
        published = source.fetch_published(currency, start, execution_date)
    except (OSError, ValueError):
        published = {}
    candidates = [day for day in published if start <= day <= execution_date]
    if execution_date in published:
        source_date, status = execution_date, "execution_day"
    elif execution_date - timedelta(days=1) in published:
        source_date, status = execution_date - timedelta(days=1), "previous_day"
    elif candidates:
        source_date, status = max(candidates), "recent_business_day"
    else:
        source_date, rate = _latest_local_rate(
            database, currency, start, execution_date
        )
        status = (
            "previous_day"
            if source_date == execution_date - timedelta(days=1)
            else "recent_business_day"
        )
        return _store_scheduled_resolution(
            database, currency, report_date, execution_date,
            source_date, rate, status
        )
    return _store_scheduled_resolution(
        database, currency, report_date, execution_date,
        source_date, published[source_date], status
    )
```

`_store_scheduled_resolution()` 在一个事务中向 `exchange_rates` 的
`report_date` 行写入汇率和四个审计字段。没有候选值时抛出明确 `ValueError`。
`_latest_local_rate()` 使用下列查询返回真实来源日期和汇率，无结果时抛出
`ValueError("no THB/CNY exchange rate within configured window")`：

```sql
SELECT COALESCE(source_date, rate_date) AS actual_source_date,
       rate_to_cny
FROM exchange_rates
WHERE currency=?
  AND COALESCE(source_date, rate_date) BETWEEN ? AND ?
ORDER BY actual_source_date DESC
LIMIT 1
```

`fetch_range()` 继续提供现有按日填充契约，但内部复用 `fetch_published()`。

- [ ] **Step 5: 验证汇率测试**

Run:
`PYTHONPATH=src .venv/bin/python -m pytest tests/integrations tests/storage/test_exchange_rate_resolution_migration.py -q`

Expected: PASS；周末填充旧契约和新增真实来源契约同时成立。

### Task 4：独立运行状态、锁和故障日志

**Files:**
- Create: `src/adwatch/operations/run_state.py`
- Create: `tests/operations/test_run_state.py`

- [ ] **Step 1: 写状态、锁和去重失败测试**

```python
def test_incident_is_logged_and_notification_is_deduplicated(tmp_path):
    store = RunStateStore(tmp_path)
    incident = Incident(
        run_id="run-1",
        occurred_at="2026-07-30T09:01:00+08:00",
        execution_date="2026-07-30",
        report_date="2026-07-29",
        stage="bridge",
        error_type="BridgeUnavailable",
        message="无法连接紫鸟 Bridge",
        exit_code=2,
    )

    store.record_incident(incident)
    assert store.should_notify(incident) is True
    store.mark_notified(incident)
    assert store.should_notify(incident) is False
    assert json.loads(store.fault_log.read_text().splitlines()[0])[
        "stage"
    ] == "bridge"
```

另写同时获取两次 `RunLock` 时第二次失败、状态原子替换和敏感字段脱敏测试。

- [ ] **Step 2: 验证失败**

Run:
`PYTHONPATH=src .venv/bin/python -m pytest tests/operations/test_run_state.py -q`

Expected: FAIL，模块尚不存在。

- [ ] **Step 3: 实现稳定接口**

```python
@dataclass(frozen=True)
class Incident:
    run_id: str
    occurred_at: str
    execution_date: str
    report_date: str
    stage: str
    error_type: str
    message: str
    exit_code: int

    @property
    def dedupe_key(self) -> str:
        return f"{self.report_date}:{self.stage}:{self.error_type}"


class RunStateStore:
    def __init__(self, data_dir: Path) -> None:
        self.operations_dir = data_dir / "operations"
        self.state_path = self.operations_dir / "daily-schedule-state.json"
        self.lock_path = self.operations_dir / "scheduled-daily.lock"
        self.fault_log = data_dir / "logs" / "adwatch-faults.jsonl"
```

使用
`fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)`
实现进程锁；状态通过 `.tmp` 文件
替换；JSONL 使用单次追加；日志消息经过固定脱敏函数去除 URL 查询参数、
Webhook 和环境变量值。

- [ ] **Step 4: 验证运维状态测试**

Run:
`PYTHONPATH=src .venv/bin/python -m pytest tests/operations/test_run_state.py -q`

Expected: PASS，临时目录外无文件写入。

### Task 5：飞书故障卡片

**Files:**
- Modify: `src/adwatch/reporting/delivery.py`
- Modify: `tests/reporting/test_delivery.py`

- [ ] **Step 1: 写故障卡片失败测试**

```python
def test_deliver_incident_sends_red_card_without_secrets(tmp_path):
    transport = CapturingTransport()
    result = deliver_incident(
        stage="bridge",
        report_date=date(2026, 7, 29),
        message="Bridge unavailable",
        log_path=tmp_path / "adwatch-faults.jsonl",
        webhook_url="https://example.test/hook",
        transport=transport,
    )

    assert result.status == "sent"
    assert transport.payload["card"]["header"]["template"] == "red"
    payload = str(transport.payload)
    assert "Bridge unavailable" in payload
    assert "https://example.test/hook" not in payload
```

- [ ] **Step 2: 验证失败**

Run:
`PYTHONPATH=src .venv/bin/python -m pytest tests/reporting/test_delivery.py::test_deliver_incident_sends_red_card_without_secrets -q`

Expected: FAIL，缺少 `deliver_incident()`。

- [ ] **Step 3: 实现故障发送**

新增 `deliver_incident()`，复用 `Transport` 与 3 次网络重试，只构造红色卡片，
返回 `DeliveryResult`。故障通知不写成 `daily-*.md`；发送失败由调用方更新
Incident 的通知状态，原始故障保持不变。

- [ ] **Step 4: 验证报告测试**

Run:
`PYTHONPATH=src .venv/bin/python -m pytest tests/reporting/test_delivery.py -q`

Expected: PASS，普通日报行为不变。

### Task 6：受管 LaunchAgent

**Files:**
- Create: `src/adwatch/operations/scheduler.py`
- Create: `tests/operations/test_scheduler.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: 写 plist 和状态失败测试**

```python
def test_rendered_plist_targets_current_project_and_scheduled_wrapper(tmp_path):
    settings = Settings(data_dir=tmp_path / "var", daily_run_time="09:00")
    plist = render_launchd_plist(tmp_path, settings)

    assert str(tmp_path / ".venv/bin/adwatch") in plist
    assert "<string>scheduled-daily</string>" in plist
    assert "<key>Hour</key><integer>9</integer>" in plist
    assert "<key>Minute</key><integer>0</integer>" in plist
    assert "<key>RunAtLoad</key><true/>" in plist
```

另写：

- 安装调用 `bootout → enable → bootstrap → print`。
- 重复安装健康配置不重复 bootstrap。
- status 检测旧目录路径、disabled、错误时区和未加载。
- uninstall 只处理已识别的 Adwatch 标签与精确 plist。

- [ ] **Step 2: 验证失败**

Run:
`PYTHONPATH=src .venv/bin/python -m pytest tests/operations/test_scheduler.py tests/test_cli.py::test_schedule_plist_uses_real_mode_and_project_runtime -q`

Expected: FAIL，旧渲染仍为 08:00、`run daily` 且 `RunAtLoad=false`。

- [ ] **Step 3: 实现调度管理**

```python
@dataclass(frozen=True)
class ScheduleStatus:
    healthy: bool
    loaded: bool
    enabled: bool
    project_dir: Path
    issues: tuple[str, ...]


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class SubprocessLaunchctl:
    def run(self, *arguments: str) -> CommandResult:
        completed = subprocess.run(
            ["launchctl", *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
        return CommandResult(
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )
```

`SubprocessLaunchctl` 使用参数数组调用 `launchctl`，不使用 `shell=True`。
安装源写入 `var/com.adwatch.daily.plist`，目标为
`~/Library/LaunchAgents/com.adwatch.daily.plist`。旧文件先复制到
`var/backups/launchagent/`。状态解析和命令执行均通过可替换适配器测试。
`ScheduleManager` 暴露 `install()`、`status()`、`repair()` 和 `uninstall()`
四个返回 `ScheduleStatus` 的方法。

- [ ] **Step 4: 验证调度测试**

Run:
`PYTHONPATH=src .venv/bin/python -m pytest tests/operations/test_scheduler.py tests/test_cli.py -q`

Expected: PASS，测试未调用真实 `launchctl`。

### Task 7：每日编排、Bridge 门禁和 `--no-notify`

**Files:**
- Create: `src/adwatch/operations/daily_run.py`
- Create: `tests/operations/test_daily_run.py`
- Modify: `src/adwatch/cli.py`
- Modify: `tests/test_daily_run_cli.py`

- [ ] **Step 1: 写严格顺序失败测试**

```python
def test_daily_run_orders_rate_bridge_collection_and_analysis():
    calls = []
    result = DailyRunService(dependencies_recording(calls)).run(
        execution_date=date(2026, 7, 30),
        report_date=date(2026, 7, 29),
        mode="ziniao",
        notify=True,
    )

    assert result.exit_code == 0
    assert calls[:4] == ["exchange_rate", "bridge", "collection", "analysis"]
```

- [ ] **Step 2: 写 Bridge 与通知失败测试**

```python
def test_bridge_failure_stops_collection_and_logs_incident():
    dependencies, calls, store = failing_bridge_dependencies()

    result = DailyRunService(dependencies).run(
        execution_date=date(2026, 7, 30),
        report_date=date(2026, 7, 29),
        mode="ziniao",
        notify=True,
    )

    assert result.exit_code == 2
    assert "collection" not in calls
    assert json.loads(store.fault_log.read_text().splitlines()[0])[
        "stage"
    ] == "bridge"
```

另写 `notify=False` 时普通日报和故障卡片都不发送，但数据库、质量报告、日报
和故障日志仍生成。

- [ ] **Step 3: 运行失败测试**

Run:
`PYTHONPATH=src .venv/bin/python -m pytest tests/operations/test_daily_run.py tests/test_daily_run_cli.py -q`

Expected: FAIL，缺少 `DailyRunService` 和 `--no-notify`。

- [ ] **Step 4: 提取并实现每日编排**

```python
@dataclass(frozen=True)
class DailyRunResult:
    exit_code: int
    status: str
    report_path: Path | None
    collection_errors: tuple[str, ...]
```

把现有 `cli.py` 的 daily 分支移动到该服务，保持订单、知识库和部分平台容错
行为。`DailyRunService.run()` 固定接收 `execution_date: date`、
`report_date: date`、`mode: str` 和 `notify: bool`，并返回
`DailyRunResult`。真实模式按配置次数调用
`ZiniaoCliClient().get_store_list()`；每次失败
后按配置延迟，最后一次失败不再等待。汇率不可用时继续采集并记录 incident；
Bridge 不可用时立即结束；平台采集异常保留其他平台并发送额外 incident。

CLI 增加：

```python
daily.add_argument("--no-notify", action="store_true")
```

显式 `run daily` 默认 `execution_date` 为北京时间当前日期，并将
`notify=not args.no_notify` 传给服务。

- [ ] **Step 5: 验证每日流程**

Run:
`PYTHONPATH=src .venv/bin/python -m pytest tests/operations/test_daily_run.py tests/test_daily_run_cli.py -q`

Expected: PASS，原有真实/模拟、部分平台、汇率不可用和订单同步测试继续通过。

### Task 8：登录补跑包装器与 CLI 生命周期

**Files:**
- Create: `src/adwatch/operations/scheduled_daily.py`
- Create: `tests/operations/test_scheduled_daily.py`
- Modify: `src/adwatch/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: 写到期判断失败测试**

```python
def test_login_after_nine_runs_missing_report():
    decision = decide_scheduled_run(
        now=datetime(2026, 7, 30, 9, 5, tzinfo=ZoneInfo("Asia/Shanghai")),
        run_time=time(9, 0),
        last_successful_report_date=None,
    )
    assert decision.should_run is True
    assert decision.report_date == date(2026, 7, 29)


def test_login_before_nine_and_already_successful_report_are_noops():
    before = decide_scheduled_run(
        now=datetime(2026, 7, 30, 8, 59, tzinfo=ZoneInfo("Asia/Shanghai")),
        run_time=time(9, 0),
        last_successful_report_date=None,
    )
    completed = decide_scheduled_run(
        now=datetime(2026, 7, 30, 9, 5, tzinfo=ZoneInfo("Asia/Shanghai")),
        run_time=time(9, 0),
        last_successful_report_date=date(2026, 7, 29),
    )
    assert before.should_run is False
    assert completed.should_run is False
```

- [ ] **Step 2: 验证失败**

Run:
`PYTHONPATH=src .venv/bin/python -m pytest tests/operations/test_scheduled_daily.py -q`

Expected: FAIL，模块尚不存在。

- [ ] **Step 3: 实现包装器**

```python
@dataclass(frozen=True)
class ScheduleDecision:
    should_run: bool
    report_date: date
    reason: str


def run_scheduled_daily(
    settings: Settings,
    *,
    now: datetime | None = None,
    daily_service: DailyRunService | None = None,
) -> int:
    current = now or datetime.now(ZoneInfo(settings.timezone))
    store = RunStateStore(settings.data_dir)
    decision = decide_scheduled_run(
        current,
        time(settings.daily_run_hour, settings.daily_run_minute),
        store.last_successful_report_date(),
    )
    if not decision.should_run:
        store.record_noop(decision.reason, current, decision.report_date)
        return 0
    with store.lock():
        result = (daily_service or DailyRunService.from_settings(settings)).run(
            execution_date=current.date(),
            report_date=decision.report_date,
            mode="ziniao",
            notify=True,
        )
        store.record_result(current, decision.report_date, result)
        return result.exit_code
```

只有 `DailyRunResult.status == "ok"` 才更新成功日期；partial 或 failed 保留
补跑资格。

- [ ] **Step 4: 接入 CLI**

增加：

```text
adwatch run scheduled-daily
adwatch schedule install
adwatch schedule status
adwatch schedule repair
adwatch schedule uninstall
adwatch schedule --print-launchd
```

安装输出固定为
`schedule=installed healthy=true project=<绝对项目路径> time=09:00`；
健康查询输出固定为 `schedule=healthy loaded=true enabled=true`；
计划运行输出固定为
`scheduled_daily=ok report_date=<YYYY-MM-DD>`。非健康状态返回 2。

- [ ] **Step 5: 验证包装器和 CLI**

Run:
`PYTHONPATH=src .venv/bin/python -m pytest tests/operations tests/test_cli.py tests/test_daily_run_cli.py -q`

Expected: PASS；帮助文本包含所有新命令。

### Task 9：全量检测、代码审查与文档同步

**Files:**
- Modify: `README.md`
- Modify: `docs/README.md`
- Create: `docs/operations/adwatch-macos-schedule.md`
- Review: 本计划列出的全部生产和测试文件

- [ ] **Step 1: 运行代码规范检测**

Run:
`PYTHONPATH=src .venv/bin/python -m ruff check src tests`

Expected: exit 0，无硬编码、未使用导入或复杂度警告。

- [ ] **Step 2: 运行全量测试**

Run:
`PYTHONPATH=src .venv/bin/python -m pytest -q`

Expected: 0 failures。

- [ ] **Step 3: 对照设计逐项审查**

检查：

- 所有时间、重试和回退参数只来自 `Settings`。
- `subprocess` 不使用 shell 字符串。
- 旧目录只用于识别迁移目标，不出现在新 plist 或运行命令。
- 日志没有 `.env`、Webhook 或凭据。
- `--no-notify` 同时覆盖普通日报与故障卡片。
- rate、Bridge、collection、analysis 顺序有测试证据。

- [ ] **Step 4: 更新用户和运维文档**

`README.md` 增加安装与状态命令；`.env.example` 已列出配置；
`docs/operations/adwatch-macos-schedule.md` 写明安装、修复、卸载、人工补跑、
重启验证和次日 09:00 验收；`docs/README.md` 添加设计、计划和运维文档链接。

- [ ] **Step 5: 检查工作树**

Run:
`git status --short`

Run:
`git diff --check`

Expected: 只存在预期文件改动，无空白错误；不还原或覆盖任务开始前的用户改动。

### Task 10：本机部署、补跑与持久化验收

**Files/State:**
- Modify outside repository: `~/Library/LaunchAgents/com.adwatch.daily.plist`
- Generate: `var/com.adwatch.daily.plist`
- Generate: `var/backups/launchagent/`
- Generate: `var/operations/`
- Generate: `var/logs/adwatch-faults.jsonl`（仅发生故障时）
- Generate: `var/reports/daily-2026-07-29.md`

- [ ] **Step 1: 保存部署前只读快照**

Run:
`launchctl print gui/$(id -u)/com.adwatch.daily`

Run:
`plutil -p ~/Library/LaunchAgents/com.adwatch.daily.plist`

Run:
`rg -n "/Users/yl/Documents/跨境电商/广告盯盘自动化" ~/Library/LaunchAgents`

Expected: 保存现有标签、旧路径和当前加载状态作为迁移证据。

- [ ] **Step 2: 安装新任务**

Run:
`.venv/bin/adwatch schedule install`

Expected:
`schedule=installed healthy=true project=/Users/yl/vscode/Cross_Border_Electronic_Commerce time=09:00`

- [ ] **Step 3: 验证没有旧任务引用**

Run:
`.venv/bin/adwatch schedule status`

Run:
`rg -n "/Users/yl/Documents/跨境电商/广告盯盘自动化" ~/Library/LaunchAgents`

Expected: status 健康；第二个命令无匹配。

- [ ] **Step 4: 从唯一代码源禁通知补跑**

Run:
`.venv/bin/adwatch run daily --mode ziniao --date 2026-07-29 --no-notify`

Expected: 当前 `var/` 生成 2026-07-29 数据、质量报告和日报；输出
`delivery=suppressed`；飞书没有新日报或故障卡片。

- [ ] **Step 5: 当前会话验证**

Run:
`launchctl print gui/$(id -u)/com.adwatch.daily`

Run:
`plutil -lint ~/Library/LaunchAgents/com.adwatch.daily.plist`

Expected: 服务路径、工作目录、09:00 和 `RunAtLoad` 正确；plist lint 通过。

- [ ] **Step 6: 取得明确确认后重新登录或重启**

重启或退出登录会中断当前协作，必须在执行前再次询问用户。恢复登录后立即运行：

`launchctl print gui/$(id -u)/com.adwatch.daily`

Expected: 未手动 bootstrap 即可找到服务，路径和触发器保持正确。

- [ ] **Step 7: 次日 09:00 验收**

在下一次真实 09:00 后运行：

`.venv/bin/adwatch schedule status`

检查 `var/operations/daily-schedule-state.json`、当天质量报告、日报和 launchd
日志。只有服务持续存在且该次真实运行有完整阶段证据，才把持久化验收标记为
完成。
