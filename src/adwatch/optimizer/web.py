"""仅监听本机的 Shopee 广告优化网页。"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit
from zoneinfo import ZoneInfo

from adwatch.optimizer.models import (
    Action,
    CampaignProfile,
    CampaignSnapshot,
    Evaluation,
    Evidence,
    Feedback,
    FeedbackStatus,
    ProfileStatus,
    Recommendation,
)
from adwatch.optimizer.repository import (
    CampaignReadModel,
    OptimizerRepository,
    ReviewReadModel,
)

DEFAULT_MAX_BODY_BYTES = 65_536
_HTML_TYPE = "text/html; charset=utf-8"
_FORM_TYPE = "application/x-www-form-urlencoded"


class _NonceStore:
    def __init__(
        self, *, ttl_seconds: int = 1800, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._values: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def issue(self, scope: str) -> str:
        now = self._clock()
        value = secrets.token_urlsafe(32)
        with self._lock:
            self._values = {
                token: item for token, item in self._values.items() if item[0] > now
            }
            self._values[value] = (now + self._ttl, scope)
        return value

    def consume(self, value: str, scope: str) -> bool:
        now = self._clock()
        with self._lock:
            item = self._values.get(value)
            if item is None:
                return False
            if item[0] <= now:
                self._values.pop(value, None)
                return False
            if not secrets.compare_digest(item[1].encode(), scope.encode()):
                return False
            self._values.pop(value, None)
            return True


def create_optimizer_server(
    repository: OptimizerRepository,
    *,
    store_id: str,
    host: str = "127.0.0.1",
    port: int = 8765,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    nonce_ttl_seconds: int = 1800,
    campaign_recent_days: int = 30,
    now_provider: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> ThreadingHTTPServer:
    """创建可由测试或 CLI 管理生命周期的本机 HTTP 服务。"""
    if host != "127.0.0.1":
        raise ValueError("optimizer web host must be 127.0.0.1")
    if isinstance(max_body_bytes, bool) or not isinstance(max_body_bytes, int):
        raise TypeError("max_body_bytes must be a positive integer")
    if max_body_bytes <= 0:
        raise ValueError("max_body_bytes must be a positive integer")
    if isinstance(nonce_ttl_seconds, bool) or not isinstance(nonce_ttl_seconds, int):
        raise TypeError("nonce_ttl_seconds must be a positive integer")
    if nonce_ttl_seconds <= 0:
        raise ValueError("nonce_ttl_seconds must be a positive integer")
    if isinstance(campaign_recent_days, bool) or not isinstance(
        campaign_recent_days, int
    ):
        raise TypeError("campaign_recent_days must be a positive integer")
    if campaign_recent_days <= 0:
        raise ValueError("campaign_recent_days must be a positive integer")
    if not store_id.strip():
        raise ValueError("store_id must not be empty")

    nonces = _NonceStore(ttl_seconds=nonce_ttl_seconds, clock=monotonic_clock)

    class Handler(BaseHTTPRequestHandler):
        server_version = "AdwatchOptimizer"
        sys_version = ""

        def _valid_host(self) -> bool:
            supplied = self.headers.get("Host", "")
            allowed = {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }
            return supplied in allowed

        def _headers(self, content_type: str, length: int) -> None:
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header(
                "Content-Security-Policy",
                f"default-src 'self'; style-src 'self' '{_STYLE_HASH}'; "
                "img-src 'self' data:; script-src 'none'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")

        def _write(self, status: int, body: str, content_type: str = _HTML_TYPE) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self._headers(content_type, len(encoded))
            self.end_headers()
            self.wfile.write(encoded)

        def _error(self, status: int, message: str) -> None:
            self._write(status, _page("错误", "/", f'<p role="alert">{html.escape(message)}</p>'))

        def _request_now(self) -> datetime | None:
            value = now_provider()
            if value.tzinfo is None or value.utcoffset() is None:
                self._error(500, "服务器时间配置无效")
                return None
            return value.astimezone(UTC)

        def do_GET(self) -> None:
            if not self._valid_host():
                self._error(400, "Host 不受信任")
                return
            request_now = self._request_now()
            if request_now is None:
                return
            parsed = urlsplit(self.path)
            if parsed.path == "/health":
                try:
                    with repository.database.connect() as connection:
                        connection.execute("SELECT 1").fetchone()
                except sqlite3.Error:
                    self._write(
                        503,
                        '{"status":"error","schema":"unknown","db":"unreadable"}',
                        "application/json; charset=utf-8",
                    )
                    return
                self._write(
                    200,
                    '{"status":"ok","schema":"ok","db":"readable"}',
                    "application/json; charset=utf-8",
                )
                return
            if parsed.path not in {"/", "/campaigns", "/reviews"}:
                self._error(404, "页面不存在")
                return
            if parsed.path == "/":
                content = _today_content(repository, store_id, request_now, nonces)
                title = "今日行动"
            elif parsed.path == "/campaigns":
                content = _campaigns_content(
                    repository,
                    store_id,
                    request_now,
                    nonces,
                    campaign_recent_days,
                )
                title = "广告活动"
            else:
                content = _reviews_content(repository, store_id, request_now)
                title = "复盘记录"
            self._write(200, _page(title, parsed.path, content))

        def do_POST(self) -> None:
            if not self._valid_host():
                self._error(400, "Host 不受信任")
                return
            request_now = self._request_now()
            if request_now is None:
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
            if content_type != _FORM_TYPE:
                self._error(415, "只接受网页表单")
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._error(400, "请求长度无效")
                return
            if length <= 0:
                self._error(400, "表单不能为空")
                return
            if length > max_body_bytes:
                self._error(413, "表单内容过大")
                return
            try:
                form = {
                    key: values[-1]
                    for key, values in parse_qs(
                        self.rfile.read(length).decode("utf-8", errors="strict"),
                        keep_blank_values=True,
                        strict_parsing=True,
                    ).items()
                }
            except (UnicodeDecodeError, ValueError):
                self._error(400, "表单编码无效")
                return
            parsed = urlsplit(self.path)
            target = _strict_target(parsed.path)
            if target is None:
                self._error(404, "页面不存在")
                return
            kind, object_id = target
            allowed = (
                _PROFILE_FIELDS if kind == "campaigns" else _FEEDBACK_FIELDS
            )
            if not set(form) <= allowed:
                self._error(400, "不支持的字段")
                return
            if kind == "campaigns":
                local_date = request_now.astimezone(ZoneInfo("Asia/Shanghai")).date()
                visible_campaigns = repository.campaign_read_models_as_of(
                    store_id,
                    recent_start=local_date - timedelta(days=campaign_recent_days - 1),
                    data_through=local_date,
                    decision_at=request_now,
                )
                if object_id not in {
                    campaign.campaign_id for campaign in visible_campaigns
                }:
                    self._error(404, "活动不存在")
                    return
            else:
                try:
                    revision = int(form.get("recommendation_revision", ""))
                except ValueError:
                    self._error(400, "建议修订号无效")
                    return
                recommendation = repository.get_recommendation(
                    store_id, object_id, revision
                )
                if (
                    recommendation is None
                    or recommendation.generated_at > request_now
                ):
                    self._error(404, "建议不存在")
                    return
            scope = (
                f"profile:{object_id}"
                if kind == "campaigns"
                else f"feedback:{object_id}:{revision}"
            )
            nonce = form.get("csrf_nonce", "")
            if not nonces.consume(nonce, scope):
                self._error(409, "表单已提交或已过期，请刷新页面")
                return
            try:
                if kind == "campaigns":
                    _save_profile(
                        repository,
                        store_id,
                        object_id,
                        form,
                        request_now.astimezone(ZoneInfo("Asia/Shanghai")).date(),
                    )
                    location = "/campaigns?saved=1"
                else:
                    _save_feedback(
                        repository, store_id, object_id, form, request_now
                    )
                    location = "/reviews?saved=1"
            except (KeyError, ValueError, InvalidOperation):
                self._error(400, "表单字段无效，请检查数值、日期和状态")
                return
            self.send_response(303)
            self.send_header("Location", location)
            self._headers(_HTML_TYPE, 0)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)


def serve_optimizer_web(
    repository: OptimizerRepository,
    *,
    store_id: str,
    host: str = "127.0.0.1",
    port: int = 8765,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    nonce_ttl_seconds: int = 1800,
    campaign_recent_days: int = 30,
) -> None:
    create_optimizer_server(
        repository,
        store_id=store_id,
        host=host,
        port=port,
        max_body_bytes=max_body_bytes,
        nonce_ttl_seconds=nonce_ttl_seconds,
        campaign_recent_days=campaign_recent_days,
    ).serve_forever()


_PROFILE_FIELDS = frozenset(
    {
        "csrf_nonce",
        "effective_from",
        "minimum_roas",
        "minimum_gmv_growth",
        "growth_intensity",
        "max_budget_step",
        "max_target_roas_step",
        "min_orders",
        "cooldown_days",
        "evaluation_days",
        "rollback_days",
        "allowed_actions",
        "note",
    }
)
_FEEDBACK_FIELDS = frozenset(
    {
        "csrf_nonce",
        "status",
        "actual_value",
        "executed_at",
        "note",
        "recommendation_revision",
    }
)


def _strict_target(path: str) -> tuple[str, str] | None:
    for kind in ("campaigns", "feedback"):
        prefix = f"/{kind}/"
        if path.startswith(prefix):
            raw = path.removeprefix(prefix)
            if not raw or "/" in raw or _invalid_percent_encoding(raw):
                return None
            try:
                value = unquote(raw, errors="strict")
            except UnicodeDecodeError:
                return None
            if not value or "%" in value or value in {".", ".."}:
                return None
            return kind, value
    return None


def _invalid_percent_encoding(value: str) -> bool:
    index = 0
    while index < len(value):
        if value[index] == "%":
            if index + 2 >= len(value) or any(
                char not in "0123456789abcdefABCDEF" for char in value[index + 1 : index + 3]
            ):
                return True
            index += 3
        else:
            index += 1
    return False


def _page(title: str, current: str, content: str) -> str:
    nav = []
    for path, label in (("/", "今日行动"), ("/campaigns", "广告活动"), ("/reviews", "复盘记录")):
        selected = ' aria-current="page"' if path == current else ""
        nav.append(f'<a href="{path}"{selected}>{label}</a>')
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}｜Shopee 广告优化</title><style>{_CSS}</style></head>
<body><a class="skip-link" href="#main">跳到主要内容</a>
<header><strong>Shopee 广告优化</strong><nav aria-label="主要导航">{' '.join(nav)}</nav></header>
<main id="main"><p class="safety">你人工修改，系统不写广告</p><h1>{html.escape(title)}</h1>{content}</main>
</body></html>"""


def _today_content(
    repository: OptimizerRepository,
    store_id: str,
    request_now: datetime,
    nonces: _NonceStore,
) -> str:
    current_date = request_now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    expected = current_date.fromordinal(current_date.toordinal() - 1)
    snapshot = repository.report_snapshot_as_of(
        store_id,
        expected,
        request_now,
    )
    fresh = (
        snapshot.actual_data_date == expected
        and snapshot.collection is not None
        and snapshot.collection.status == "success"
    )
    status = "CURRENT" if fresh else "STALE / UNVERIFIED"
    collection_status = "未采集" if snapshot.collection is None else snapshot.collection.status
    collected_at = (
        "—"
        if snapshot.collection is None or snapshot.collection.attempted_at is None
        else snapshot.collection.attempted_at.isoformat()
    )
    actual_data_date = (
        "—" if snapshot.actual_data_date is None else snapshot.actual_data_date.isoformat()
    )
    qualities = [
        evidence.quality.status.value
        for recommendation in snapshot.recommendations
        for evidence in recommendation.evidence
    ]
    quality_order = {"healthy": 0, "degraded": 1, "stale": 2, "invalid": 3}
    overall_quality = (
        "无可用质量评估"
        if not qualities
        else max(qualities, key=quality_order.__getitem__)
    )
    cards = "<p>今天没有经过新鲜度门禁验证的建议。</p>"
    structures = "<p>暂无结构调整待办。</p>"
    if fresh and snapshot.recommendations:
        ordered = sorted(snapshot.recommendations, key=_recommendation_sort_key)
        normal = [item for item in ordered if item.action not in {Action.CREATE_GROUP, Action.SPLIT_GROUP}]
        structure = [item for item in ordered if item.action in {Action.CREATE_GROUP, Action.SPLIT_GROUP}]
        cards = "".join(_recommendation_card(item, nonces) for item in normal) or "<p>暂无参数建议。</p>"
        structures = "".join(_recommendation_card(item, nonces) for item in structure) or structures
    return f'<section aria-labelledby="status"><h2 id="status">数据状态</h2><p>{status}</p><p>采集状态：{collection_status}；最近采集时间：{collected_at}</p><p>预期数据日：{expected.isoformat()}；实际数据日：{actual_data_date}</p><p>整体数据健康：{overall_quality}</p></section><section><h2>今日建议</h2>{cards}</section><section><h2>结构调整待办</h2>{structures}</section>'


def _recommendation_sort_key(item: Recommendation) -> tuple[int, int, str]:
    order = {
        Action.PAUSE: 0,
        Action.INCREASE_BUDGET: 1,
        Action.DECREASE_TARGET_ROAS: 1,
        Action.RESUME: 1,
        Action.CREATE_GROUP: 1,
        Action.SPLIT_GROUP: 1,
        Action.DECREASE_BUDGET: 2,
        Action.INCREASE_TARGET_ROAS: 2,
        Action.HOLD: 3,
    }
    return order[item.action], item.priority, item.campaign_id


def _recommendation_card(item: Recommendation, nonces: _NonceStore) -> str:
    evidence = "".join(_evidence_detail(value) for value in item.evidence)
    current = "—" if item.current_value is None else str(item.current_value)
    proposed = "—" if item.proposed_value is None else str(item.proposed_value)
    change = "—" if item.change_ratio is None else str(item.change_ratio)
    success_roas = "未设置" if item.success_condition.minimum_roas is None else str(item.success_condition.minimum_roas)
    rollback_roas = "未设置" if item.rollback_condition.roas_below is None else str(item.rollback_condition.roas_below)
    nonce_scope = f"feedback:{item.recommendation_id}:{item.revision}"
    execution_nonce = nonces.issue(nonce_scope)
    non_execution_nonce = nonces.issue(nonce_scope)
    return f"""<article><h3>{html.escape(item.campaign_id)}</h3>
<p><strong>{html.escape(item.action.value)}</strong>；置信度：{item.confidence.value}</p>
<p>生成时间：{item.generated_at.isoformat()}；建议数据日：{item.data_date.isoformat()}；优先级：{item.priority}；预期效果：{item.expected_impact}</p>
<p>当前值：{current}；建议值：{proposed}；变动：{change}</p>
<p>规则版本：{html.escape(item.rule_version)}</p><div><strong>证据</strong>{evidence}</div><p>风险：{item.risk}；观察：{item.observation_days} 天</p>
<p>成功条件：ROAS ≥ {success_roas}，GMV 增长 ≥ {item.success_condition.minimum_gmv_growth}；成功观察：{item.success_condition.observation_days} 天</p>
<p>回退条件：ROAS &lt; {rollback_roas} 连续 {item.rollback_condition.consecutive_days} 天</p>
<form method="post" action="/feedback/{quote(item.recommendation_id, safe='')}">
<input type="hidden" name="csrf_nonce" value="{html.escape(execution_nonce, quote=True)}">
<input type="hidden" name="recommendation_revision" value="{item.revision}">
<fieldset><legend>记录执行结果</legend><label>执行状态<select name="status"><option value="executed">按建议执行</option><option value="modified">修改后执行</option></select></label>
<label>实际值<input name="actual_value" inputmode="decimal"></label>
<label>执行时间<input type="datetime-local" name="executed_at"></label>
<label>备注<textarea name="note"></textarea></label></fieldset><button>保存执行结果</button></form>
<form method="post" action="/feedback/{quote(item.recommendation_id, safe='')}">
<input type="hidden" name="csrf_nonce" value="{html.escape(non_execution_nonce, quote=True)}">
<input type="hidden" name="recommendation_revision" value="{item.revision}">
<fieldset><legend>记录非执行反馈</legend><label>反馈状态<select name="status"><option value="deferred">暂不执行</option><option value="rejected">拒绝</option></select></label>
<label>备注<textarea name="note"></textarea></label></fieldset><button>保存非执行反馈</button></form></article>"""


def _evidence_detail(evidence: Evidence) -> str:
    summary = html.escape(evidence.summary)
    missing = ", ".join(html.escape(value) for value in evidence.quality.missing_fields)
    rule_summary = html.escape(_rule_input_summary(evidence.rule_inputs))
    audit_items = "".join(
        f"<dt>{html.escape(key)}</dt><dd><code>{html.escape(_decoded_rule_input(value)[1])}</code></dd>"
        for key, value in evidence.rule_inputs
    )
    audit = (
        f"<details><summary>技术审计详情</summary><dl>{audit_items}</dl></details>"
        if audit_items
        else ""
    )
    quality_note = html.escape(evidence.quality.note) or "无"
    return f"""<section class="evidence"><p>{summary}</p>
<p>短窗：{evidence.short_window.start_date.isoformat()} 至 {evidence.short_window.end_date.isoformat()}；长窗：{evidence.long_window.start_date.isoformat()} 至 {evidence.long_window.end_date.isoformat()}</p>
<p>质量：{evidence.quality.status.value}；质量评估时间：{evidence.quality.assessed_at.isoformat()}；质量说明：{quality_note}</p>
<p>缺失：{missing or '无'}</p><p>策略摘要：{rule_summary}</p>{audit}</section>"""


def _rule_input_summary(rule_inputs: tuple[tuple[str, str], ...]) -> str:
    values = {key: _decoded_rule_input(value)[0] for key, value in rule_inputs}
    template = _mapping(values.get("policy_template"))
    profile = _mapping(values.get("profile"))
    snapshot = _mapping(values.get("snapshot"))
    parts: list[str] = []
    short_days = _integer_text(template.get("short_window_days"))
    long_days = _integer_text(template.get("long_window_days"))
    if short_days and long_days:
        parts.append(f"短窗 {short_days} 天、长窗 {long_days} 天")
    history_days = _integer_text(template.get("min_history_days"))
    min_orders = _integer_text(template.get("min_orders"))
    if history_days and min_orders:
        parts.append(f"最低样本 {history_days} 天 / {min_orders} 单")
    budget_step = _percent_text(template.get("default_budget_step"))
    max_budget_step = _percent_text(template.get("global_max_budget_step"))
    if budget_step and max_budget_step:
        parts.append(f"预算调整 {budget_step}（最多 {max_budget_step}）")
    minimum_roas = _number_text(profile.get("minimum_roas"))
    if minimum_roas:
        parts.append(f"最低 ROAS {minimum_roas}")
    current_budget = _number_text(snapshot.get("current_budget"))
    if current_budget:
        currency = str(snapshot.get("currency") or "").strip()
        parts.append(f"当前预算 {current_budget}" + (f" {currency}" if currency else ""))
    target_roas = _number_text(snapshot.get("target_roas"))
    if target_roas:
        parts.append(f"目标 ROAS {target_roas}")
    status = str(snapshot.get("status") or "").strip()
    if status:
        parts.append(f"活动状态 {status}")
    return "；".join(parts) or "无可读摘要"


def _decoded_rule_input(value: str) -> tuple[object | None, str]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None, value
    return parsed, json.dumps(
        parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _integer_text(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return str(parsed) if parsed >= 0 else None


def _number_text(value: object) -> str | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite():
        return None
    rendered = format(parsed, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _percent_text(value: object) -> str | None:
    parsed = _number_text(value)
    if parsed is None:
        return None
    return f"{_number_text(Decimal(parsed) * 100)}%"


def _campaigns_content(
    repository: OptimizerRepository,
    store_id: str,
    request_now: datetime,
    nonces: _NonceStore,
    campaign_recent_days: int,
) -> str:
    current_date = request_now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    campaigns = repository.campaign_read_models_as_of(
        store_id,
        recent_start=current_date - timedelta(days=campaign_recent_days - 1),
        data_through=current_date,
        decision_at=request_now,
    )
    if not campaigns:
        return """<p>活动控制面与策略版本仅作用于已配置的 Shopee 店铺。</p>
<section><h2>活动策略</h2><p>暂无真实广告活动；完成采集后可逐活动配置。</p></section>"""
    return '<p>活动控制面与策略版本仅作用于已配置的 Shopee 店铺。</p><section><h2>活动策略</h2>' + "".join(
        _campaign_card(campaign, nonces) for campaign in campaigns
    ) + "</section>"


def _campaign_card(campaign: CampaignReadModel, nonces: _NonceStore) -> str:
    snapshot = campaign.snapshot
    metric = campaign.metric
    campaign_name = (
        snapshot.campaign_name
        if snapshot is not None
        else metric.campaign_name if metric is not None else ""
    )
    heading = html.escape(_campaign_label(campaign_name, campaign.campaign_id))
    control = (
        "<p>暂无控制面快照。</p>"
        if snapshot is None
        else f"<p>状态：{snapshot.status.value}；预算：{_metric_text(snapshot.current_budget)} {snapshot.currency}；目标 ROAS：{_metric_text(snapshot.target_roas)}</p>"
    )
    performance = (
        "<p>暂无近期活动指标。</p>"
        if metric is None
        else f"<p>最近数据日：{metric.data_date.isoformat()}；花费：{metric.spend} {metric.currency}；成交额：{metric.gmv} {metric.currency}；ROAS：{_metric_text(metric.roas)}；订单：{metric.orders}</p>"
    )
    history = (
        "<li>暂无历史版本</li>"
        if not campaign.profile_versions
        else "".join(
            f"<li>{profile.effective_from.isoformat()} · {profile.status.value} · 最低 ROAS {_metric_text(profile.minimum_roas)} · {html.escape(profile.note) or '—'}</li>"
            for profile in campaign.profile_versions
        )
    )
    form = (
        _new_profile_form(
            campaign.campaign_id,
            nonces.issue(f"profile:{campaign.campaign_id}"),
            snapshot,
        )
        if campaign.profile is None
        else _profile_form(
            campaign.profile,
            nonces.issue(f"profile:{campaign.campaign_id}"),
        )
    )
    return f"""<article><h3>{heading}</h3>
{control}{performance}<h4>策略版本历史</h4><ol>{history}</ol>{form}</article>"""


def _campaign_label(name: str, campaign_id: str) -> str:
    cleaned = name.strip()
    if not cleaned or all(character in ".。…·-—–_" for character in cleaned):
        return f"未获取活动名称（{campaign_id}）"
    return f"{cleaned}（{campaign_id}）"


def _reviews_content(
    repository: OptimizerRepository, store_id: str, request_now: datetime
) -> str:
    records = repository.review_read_models_as_of(store_id, request_now)
    if not records:
        return '<section><h2>人工反馈与效果</h2><p>暂无复盘记录。</p></section>'
    return '<section><h2>人工反馈与效果</h2>' + "".join(
        _review_card(item) for item in records
    ) + "</section>"


def _review_card(record: ReviewReadModel) -> str:
    feedback = record.feedback
    recommendation = record.recommendation
    latest = record.evaluation
    if feedback.status is FeedbackStatus.EXECUTED:
        actual = recommendation.proposed_value
    elif feedback.status is FeedbackStatus.MODIFIED:
        actual = feedback.actual_value
    else:
        actual = "—"
    note = html.escape(feedback.note)
    review = (
        '<div class="trend-empty"><strong>尚无效果评估</strong>'
        '<p>观察期结束后再显示趋势。</p></div>'
        if latest is None
        else _evaluation_review(
            feedback, recommendation, latest, record.rollback_triggered
        )
    )
    effectiveness = (
        "0/0（无已完成评估）"
        if record.completed_evaluation_count == 0
        else f"{record.effective_count}/{record.completed_evaluation_count}"
    )
    time_label = (
        "执行时间"
        if feedback.status in {FeedbackStatus.EXECUTED, FeedbackStatus.MODIFIED}
        else "反馈记录时间"
    )
    return f"""<article><h3>{html.escape(recommendation.campaign_id)}</h3>
<p>建议：{html.escape(recommendation.recommendation_id)}；建议修订：{recommendation.revision}</p>
<p>动作：{html.escape(recommendation.action.value)}；建议值：{recommendation.proposed_value}；实际值：{actual}</p>
<p>人工状态：{html.escape(feedback.status.value)}；{time_label}：{feedback.executed_at.isoformat()}</p>
<p>同活动同动作：采纳率：{record.adopted_count}/{record.total_recommendations}（采纳建议/总建议）；已反馈：{record.feedback_count}/{record.total_recommendations}（有反馈建议/总建议）；有效率：{effectiveness}（有效/已完成评估）</p>
<p>备注：{note or '—'}</p>{review}</article>"""


def _evaluation_review(
    feedback: Feedback,
    recommendation: Recommendation,
    evaluation: Evaluation,
    rollback_triggered: bool | None,
) -> str:
    baseline = evaluation.baseline_stats
    after = evaluation.after_stats
    status_labels = {
        "effective": "有效",
        "no_change": "无明显变化",
        "negative": "负向",
        "insufficient_data": "数据不足",
        "pending": "待评估",
    }
    metrics = (
        ("成交额", baseline.gmv, after.gmv),
        ("花费", baseline.spend, after.spend),
        ("ROAS", baseline.roas, after.roas),
        ("CPA", baseline.cpa, after.cpa),
        ("订单", Decimal(baseline.orders), Decimal(after.orders)),
    )
    chart_prefix = hashlib.sha256(feedback.feedback_id.encode()).hexdigest()[:12]
    summary = "".join(
        f"<li>{html.escape(label)}：{_metric_text(before)} → {_metric_text(current)}</li>"
        for label, before, current in metrics
    )
    charts = "".join(
        _trend_svg(f"trend-{chart_prefix}-{index}", label, before, current)
        for index, (label, before, current) in enumerate(metrics)
    )
    evaluated_note = html.escape(evaluation.note)
    status = html.escape(evaluation.status.value)
    label = status_labels[evaluation.status.value]
    rollback = (
        "未评估"
        if rollback_triggered is None
        else "已触发" if rollback_triggered else "未触发"
    )
    return f"""<section class="evaluation" aria-label="效果评估">
<h4>评估结论</h4><p><strong>{status}</strong>（{label}）；评估修订：{evaluation.revision}</p>
<p>回退判定：{rollback}</p>
<p>建议生成日：{recommendation.data_date.isoformat()}；人工执行：{feedback.executed_at.date().isoformat()}</p>
<p>基线窗口：{evaluation.baseline_start.isoformat()} 至 {evaluation.baseline_end.isoformat()}；观察窗口：{evaluation.observation_start.isoformat()} 至 {evaluation.observation_end.isoformat()}</p>
<h5>趋势摘要</h5><ul class="trend-summary">{summary}</ul>
<div class="trend-grid">{charts}</div>
<p>评估备注：{evaluated_note or '—'}</p></section>"""


def _metric_text(value: Decimal | None) -> str:
    return "暂无数据" if value is None else str(value)


def _trend_svg(
    chart_id: str,
    label: str,
    before: Decimal | None,
    after: Decimal | None,
) -> str:
    escaped_label = html.escape(label)
    if before is None or after is None:
        return f'<p class="trend-empty">{escaped_label}：暂无可比较数据。</p>'
    peak = max(before, after)

    def y(value: Decimal) -> int:
        if peak == 0:
            return 58
        return 70 - int(value / peak * Decimal(44))

    before_y = y(before)
    after_y = y(after)
    title_id = f"{chart_id}-title"
    desc_id = f"{chart_id}-desc"
    return f"""<figure><svg role="img" aria-labelledby="{title_id} {desc_id}" viewBox="0 0 260 112">
<title id="{title_id}">{escaped_label}趋势</title>
<desc id="{desc_id}">{escaped_label}从基线 {_metric_text(before)} 变为观察期 {_metric_text(after)}</desc>
<line class="trend-axis" x1="30" y1="78" x2="230" y2="78" />
<polyline class="trend-line" points="55,{before_y} 205,{after_y}" />
<circle class="trend-point" cx="55" cy="{before_y}" r="5" />
<rect class="trend-point-after" x="200" y="{after_y - 5}" width="10" height="10" />
<text x="55" y="96" text-anchor="middle">基线 {before}</text>
<text x="205" y="96" text-anchor="middle">观察 {after}</text>
</svg><figcaption>{escaped_label}：基线到观察期</figcaption></figure>"""


def _profile_form(profile: CampaignProfile, nonce: str) -> str:
    escaped_id = html.escape(profile.campaign_id)
    minimum_roas = "" if profile.minimum_roas is None else str(profile.minimum_roas)
    actions = ",".join(sorted(action.value for action in profile.allowed_actions))
    needs_floor = '<strong class="warning">需要补充最低 ROAS</strong>' if profile.status is ProfileStatus.NEEDS_FLOOR else ""
    return f"""<article><h3>{escaped_id}</h3>{needs_floor}
<p>状态：{profile.status.value}；生效日：{profile.effective_from.isoformat()}</p>
<form method="post" action="/campaigns/{quote(profile.campaign_id, safe='')}">
<input type="hidden" name="csrf_nonce" value="{html.escape(nonce, quote=True)}">
<fieldset><legend>策略参数</legend>
<label>生效日期<input type="date" name="effective_from" value="{profile.effective_from.isoformat()}"></label>
<label>最低 ROAS（倍率）<input name="minimum_roas" inputmode="decimal" value="{minimum_roas}"></label>
<label>最低 GMV 增长（比例，0.20 = 20%）<input name="minimum_gmv_growth" inputmode="decimal" value="{profile.minimum_gmv_growth}"></label>
<label>扩量强度（比例，0.20 = 20%）<input name="growth_intensity" inputmode="decimal" value="{profile.growth_intensity}"></label>
<label>预算最大步长（比例，0.20 = 20%）<input name="max_budget_step" inputmode="decimal" value="{profile.max_budget_step}"></label>
<label>目标 ROAS 最大步长（比例，0.20 = 20%）<input name="max_target_roas_step" inputmode="decimal" value="{profile.max_target_roas_step}"></label>
<label>最低订单样本<input type="number" name="min_orders" value="{profile.min_orders}"></label>
<label>冷静期（天）<input type="number" name="cooldown_days" value="{profile.cooldown_days}"></label>
<label>评估期（天）<input type="number" name="evaluation_days" value="{profile.evaluation_days}"></label>
<label>回退连续天数<input type="number" name="rollback_days" value="{profile.rollback_days}"></label>
<label>允许动作（逗号分隔）<input name="allowed_actions" value="{html.escape(actions, quote=True)}"></label>
<label>备注<textarea name="note">{html.escape(profile.note)}</textarea></label>
</fieldset><button>保存新版本</button></form></article>"""


def _new_profile_form(
    campaign_id: str, nonce: str, snapshot: CampaignSnapshot | None
) -> str:
    effective_from = "" if snapshot is None else snapshot.data_date.isoformat()
    minimum_roas = (
        ""
        if snapshot is None or snapshot.target_roas is None
        else str(snapshot.target_roas)
    )
    escaped_id = quote(campaign_id, safe="")
    return f"""<p>暂无活动策略档案；请基于真实活动参数创建。</p>
<form method="post" action="/campaigns/{escaped_id}">
<input type="hidden" name="csrf_nonce" value="{html.escape(nonce, quote=True)}">
<fieldset><legend>创建策略参数</legend>
<label>生效日期<input required type="date" name="effective_from" value="{effective_from}"></label>
<label>最低 ROAS（倍率）<input name="minimum_roas" inputmode="decimal" value="{minimum_roas}"></label>
<label>最低 GMV 增长（比例，0.20 = 20%）<input required name="minimum_gmv_growth" inputmode="decimal" value=""></label>
<label>扩量强度（比例，0.20 = 20%）<input required name="growth_intensity" inputmode="decimal" value=""></label>
<label>预算最大步长（比例，0.20 = 20%）<input required name="max_budget_step" inputmode="decimal" value=""></label>
<label>目标 ROAS 最大步长（比例，0.20 = 20%）<input required name="max_target_roas_step" inputmode="decimal" value=""></label>
<label>最低订单样本<input required type="number" name="min_orders" value=""></label>
<label>冷静期（天）<input required type="number" name="cooldown_days" value=""></label>
<label>评估期（天）<input required type="number" name="evaluation_days" value=""></label>
<label>回退连续天数<input required type="number" name="rollback_days" value=""></label>
<label>允许动作（逗号分隔）<input required name="allowed_actions" value=""></label>
<label>备注<textarea name="note"></textarea></label>
</fieldset><button>创建策略档案</button></form>"""


def _save_profile(
    repository: OptimizerRepository,
    store_id: str,
    campaign_id: str,
    form: dict[str, str],
    current_date: date,
) -> None:
    effective_from = date.fromisoformat(form["effective_from"])
    if effective_from > current_date:
        raise ValueError("future profile")
    raw_floor = form["minimum_roas"].strip()
    minimum_roas = None if not raw_floor else Decimal(raw_floor)
    allowed = frozenset(
        Action(value.strip())
        for value in form["allowed_actions"].split(",")
        if value.strip()
    )
    repository.save_profile(
        CampaignProfile(
            store_id=store_id,
            campaign_id=campaign_id,
            status=(
                ProfileStatus.NEEDS_FLOOR
                if minimum_roas is None
                else ProfileStatus.FORMAL
            ),
            effective_from=effective_from,
            minimum_roas=minimum_roas,
            minimum_gmv_growth=Decimal(form["minimum_gmv_growth"]),
            growth_intensity=Decimal(form["growth_intensity"]),
            max_budget_step=Decimal(form["max_budget_step"]),
            max_target_roas_step=Decimal(form["max_target_roas_step"]),
            min_orders=int(form["min_orders"]),
            cooldown_days=int(form["cooldown_days"]),
            evaluation_days=int(form["evaluation_days"]),
            rollback_days=int(form["rollback_days"]),
            allowed_actions=allowed,
            note=form.get("note", ""),
        )
    )


def _save_feedback(
    repository: OptimizerRepository,
    store_id: str,
    recommendation_id: str,
    form: dict[str, str],
    request_now: datetime,
) -> None:
    """保存人工反馈。

    ``Feedback.executed_at`` 为兼容字段：执行类状态记录用户填写的执行时间；
    deferred/rejected 没有执行动作，改为记录本次请求的反馈时间。
    """
    status = FeedbackStatus(form["status"])
    raw_actual = form.get("actual_value", "").strip()
    actual = Decimal(raw_actual) if raw_actual else None
    if status in {FeedbackStatus.DEFERRED, FeedbackStatus.REJECTED}:
        if actual is not None:
            raise ValueError("non-execution feedback must not include actual_value")
        supplied_time = form.get("executed_at", "").strip()
        if supplied_time:
            raise ValueError("non-execution feedback must not include executed_at")
        executed_at = request_now
    else:
        local_time = datetime.fromisoformat(form["executed_at"])
        if local_time.tzinfo is not None:
            raise ValueError("datetime-local must not include offset")
        executed_at = local_time.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        if executed_at > request_now:
            raise ValueError("feedback execution time must not be in the future")
    revision = int(form["recommendation_revision"])
    digest = hashlib.sha256(
        f"{store_id}\0{recommendation_id}\0{revision}\0{status.value}\0{executed_at.isoformat()}".encode()
    ).hexdigest()[:24]
    repository.save_feedback(
        store_id,
        Feedback(
            feedback_id=f"web-{digest}",
            recommendation_id=recommendation_id,
            status=status,
            actual_value=actual,
            executed_at=executed_at,
            note=form.get("note", ""),
            recommendation_revision=revision,
        ),
    )


_CSS = """
:root{color-scheme:light dark;--brand:#EA580C;--cta:#2563EB;--on-cta:#fff;--bg:#fff7ed;--surface:#fff;--text:#1c1917;--muted:#57534e;--line:#d6d3d1}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header,main{max-width:72rem;margin:auto;padding:1rem}header{display:flex;justify-content:space-between;gap:1rem;align-items:center;border-bottom:3px solid var(--brand)}nav{display:flex;gap:.5rem;flex-wrap:wrap}a,button,select,input,textarea{min-height:44px}a{padding:.6rem;color:var(--cta)}a[aria-current=page]{font-weight:700;text-decoration-thickness:3px}.skip-link{position:absolute;left:-9999px}.skip-link:focus{left:1rem;top:1rem;background:var(--surface);z-index:2}a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:3px solid var(--cta);outline-offset:2px}.safety{border-left:4px solid var(--brand);padding:.75rem;background:var(--surface)}article,section{background:var(--surface);border:1px solid var(--line);border-radius:.5rem;padding:1rem;margin:1rem 0}label{display:grid;gap:.25rem;margin:.75rem 0}button{border:0;border-radius:.375rem;background:var(--cta);color:var(--on-cta);padding:.65rem 1rem;font-weight:700;cursor:pointer}table{width:100%;border-collapse:collapse}.table-scroll{overflow-x:auto}th,td{text-align:left;padding:.5rem;border-bottom:1px solid var(--line)}.evaluation{background:var(--bg)}.trend-summary{font-variant-numeric:tabular-nums}.trend-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,15rem),1fr));gap:1rem}.trend-grid figure{margin:0;padding:.5rem;border:1px solid var(--line);border-radius:.375rem}.trend-grid svg{display:block;width:100%;height:auto}.trend-axis{stroke:var(--line);stroke-width:2}.trend-line{fill:none;stroke:var(--cta);stroke-width:4}.trend-point{fill:var(--surface);stroke:var(--cta);stroke-width:4}.trend-point-after{fill:var(--brand)}.trend-grid text{fill:var(--text);font-size:11px}.trend-grid figcaption,.trend-empty{color:var(--muted)}
@media(prefers-color-scheme:dark){:root{--cta:#93C5FD;--on-cta:#172554;--bg:#1c1917;--surface:#292524;--text:#fafaf9;--muted:#d6d3d1;--line:#57534e}}
@media (max-width: 375px){header{align-items:flex-start;flex-direction:column}nav{width:100%}nav a{flex:1;text-align:center}main{padding:.75rem}article,section{padding:.75rem}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
"""

_STYLE_HASH = "sha256-" + base64.b64encode(hashlib.sha256(_CSS.encode()).digest()).decode()
