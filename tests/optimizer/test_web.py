from __future__ import annotations

import http.client
import threading
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

import pytest

from adwatch.optimizer.database import OptimizerDatabase
from adwatch.optimizer.models import (
    Action,
    AdMetric,
    CampaignProfile,
    CampaignSnapshot,
    CampaignStatus,
    Confidence,
    DataQuality,
    DataQualityStatus,
    Evaluation,
    EvaluationStatus,
    Evidence,
    Feedback,
    FeedbackStatus,
    MetricGrain,
    ProfileStatus,
    Recommendation,
    RollbackCondition,
    SuccessCondition,
    WindowStats,
)
from adwatch.optimizer.repository import OptimizerRepository
from adwatch.optimizer.web import _NonceStore, create_optimizer_server


@pytest.fixture
def repository(tmp_path: Path) -> OptimizerRepository:
    database = OptimizerDatabase(tmp_path / "optimizer.sqlite3")
    database.migrate()
    return OptimizerRepository(database)


@pytest.fixture
def web_server(repository: OptimizerRepository):
    server = create_optimizer_server(
        repository,
        store_id="store-1",
        host="127.0.0.1",
        port=0,
        max_body_bytes=1024,
        campaign_recent_days=8,
        now_provider=lambda: datetime(2026, 9, 10, 4, tzinfo=UTC),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _request(server, method: str, path: str, *, body: str = "", headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
    request_headers = {"Host": f"127.0.0.1:{server.server_port}"}
    request_headers.update(headers or {})
    connection.request(method, path, body=body.encode(), headers=request_headers)
    response = connection.getresponse()
    content = response.read().decode()
    result = response.status, dict(response.getheaders()), content
    connection.close()
    return result


def _contrast(foreground: str, background: str) -> float:
    def luminance(value: str) -> float:
        channels = [int(value[index : index + 2], 16) / 255 for index in (0, 2, 4)]
        linear = [
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    first, second = sorted(
        (luminance(foreground), luminance(background)), reverse=True
    )
    return (first + 0.05) / (second + 0.05)


def _profile(*, note: str = "") -> CampaignProfile:
    return CampaignProfile(
        "store-1", "campaign/一", ProfileStatus.FORMAL, date(2026, 9, 9),
        Decimal("2.5"), Decimal("0.05"), Decimal("0.5"), Decimal("0.2"),
        Decimal("0.1"), 3, 2, 7, 3,
        frozenset({Action.INCREASE_BUDGET, Action.HOLD}), note,
    )


def _recommendation() -> Recommendation:
    window = WindowStats(date(2026, 9, 3), date(2026, 9, 9), Decimal(10), Decimal(50), 2)
    quality = DataQuality(
        DataQualityStatus.DEGRADED,
        datetime(2026, 9, 10, tzinfo=UTC),
        missing_fields=("target_roas",),
        note="需补充目标",
    )
    return Recommendation(
        "recommendation/一", "store-1", "campaign/一", date(2026, 9, 9),
        Action.INCREASE_BUDGET, Confidence.HIGH, 1, Decimal(100), Decimal(120),
        Decimal("0.2"), (Evidence("trend", "证据 <script>alert(1)</script>", window, window, quality, (("floor", "2.5"),)),),
        SuccessCondition(Decimal("2.5"), Decimal("0.05"), 3),
        RollbackCondition(Decimal(2), 2), Decimal("0.1"), Decimal("0.2"), 3,
        "v1", "input-1", 1, datetime(2026, 9, 10, tzinfo=UTC),
    )


def _make_today_visible(repository: OptimizerRepository) -> None:
    repository.upsert_metric(
        AdMetric(
            "store-1", "campaign/一", "活动", "__ALL__", date(2026, 9, 9),
            "THB", Decimal(10), Decimal(50), 2,
            datetime(2026, 9, 10, tzinfo=UTC), "test", MetricGrain.CAMPAIGN,
        )
    )
    repository.record_collection_day(
        "store-1", date(2026, 9, 9), "success",
        attempted_at=datetime(2026, 9, 10, tzinfo=UTC),
    )


@pytest.mark.parametrize("path", ["/", "/campaigns", "/reviews"])
def test_html_routes_are_local_accessible_pages(web_server, path: str) -> None:
    status, headers, body = _request(web_server, "GET", path)

    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert "Content-Security-Policy" in headers
    assert "unsafe-inline" not in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert '<a class="skip-link" href="#main">跳到主要内容</a>' in body
    assert "你人工修改，系统不写广告" in body
    assert "@media (max-width: 375px)" in body
    assert "prefers-reduced-motion" in body


def test_navigation_marks_current_page_and_health_is_non_sensitive(web_server) -> None:
    status, _, body = _request(web_server, "GET", "/campaigns")
    assert status == 200
    assert 'href="/campaigns" aria-current="page"' in body

    status, headers, body = _request(web_server, "GET", "/health")
    assert status == 200
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert body == '{"status":"ok","schema":"ok","db":"readable"}'
    assert "store-1" not in body
    assert "sqlite" not in body.lower()


def test_dark_theme_link_and_button_contrast_meets_wcag_aa(web_server) -> None:
    _, _, page = _request(web_server, "GET", "/")

    assert "--cta:#93C5FD" in page
    assert "--on-cta:#172554" in page
    assert _contrast("93C5FD", "292524") >= 4.5
    assert _contrast("172554", "93C5FD") >= 4.5


def test_rejects_rebinding_unknown_and_malformed_paths(web_server) -> None:
    assert _request(web_server, "GET", "/", headers={"Host": "evil.test"})[0] == 400
    assert _request(web_server, "GET", "/missing")[0] == 404
    assert _request(web_server, "GET", "/campaigns/%ZZ")[0] == 404


def test_post_requires_form_content_type_and_enforces_size(web_server) -> None:
    assert _request(web_server, "POST", "/feedback/x", body="x=1")[0] == 415
    status, _, _ = _request(
        web_server,
        "POST",
        "/feedback/x",
        body="x" * 1025,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Content-Length": "1025",
        },
    )
    assert status == 413


def test_profile_post_rejects_unknown_fields_without_consuming_nonce(
    repository: OptimizerRepository, web_server
) -> None:
    repository.save_profile(_profile())
    _, _, page = _request(web_server, "GET", "/campaigns")
    nonce = page.split('name="csrf_nonce" value="', 1)[1].split('"', 1)[0]
    body = urlencode({"csrf_nonce": nonce, "write_ad": "true"})
    status, _, response = _request(
        web_server,
        "POST",
        "/campaigns/campaign-1",
        body=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert status == 400
    assert "不支持的字段" in response


def test_server_refuses_non_loopback_binding(repository: OptimizerRepository) -> None:
    with pytest.raises(ValueError, match="127.0.0.1"):
        create_optimizer_server(repository, store_id="store-1", host="0.0.0.0")


def test_request_uses_one_aware_now_and_hides_future_collection_metric_recommendation(
    repository: OptimizerRepository,
) -> None:
    future = datetime(2026, 9, 10, 5, tzinfo=UTC)
    repository.upsert_metric(
        AdMetric(
            "store-1", "future-campaign", "未来活动", "__ALL__",
            date(2026, 9, 9), "THB", Decimal(10), Decimal(50), 2,
            future, "test", MetricGrain.CAMPAIGN,
        )
    )
    repository.record_collection_day(
        "store-1", date(2026, 9, 9), "success", attempted_at=future
    )
    repository.save_recommendation(
        replace(
            _recommendation(),
            recommendation_id="future-recommendation",
            campaign_id="future-campaign",
            generated_at=future,
        )
    )
    calls = 0

    def now_provider() -> datetime:
        nonlocal calls
        calls += 1
        return datetime(2026, 9, 10, 4, tzinfo=UTC)

    server = create_optimizer_server(
        repository, store_id="store-1", port=0, now_provider=now_provider
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, page = _request(server, "GET", "/")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert status == 200
    assert calls == 1
    assert "STALE / UNVERIFIED" in page
    assert "future-campaign" not in page
    assert "future-recommendation" not in page


def test_server_rejects_naive_now_provider(repository: OptimizerRepository) -> None:
    server = create_optimizer_server(
        repository,
        store_id="store-1",
        port=0,
        now_provider=lambda: datetime(2026, 9, 10, 4, tzinfo=UTC).replace(tzinfo=None),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert _request(server, "GET", "/")[0] == 500
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_nonce_scope_mismatch_does_not_consume_and_expiry_uses_injected_clock() -> None:
    tick = [10.0]
    nonces = _NonceStore(ttl_seconds=2, clock=lambda: tick[0])
    token = nonces.issue("feedback:recommendation-1:1")

    assert not nonces.consume(token, "feedback:recommendation-1:2")
    assert not nonces.consume(token, "profile:recommendation-1")
    assert nonces.consume(token, "feedback:recommendation-1:1")
    assert not nonces.consume(token, "feedback:recommendation-1:1")

    expired = nonces.issue("profile:campaign-1")
    tick[0] = 12.01
    assert not nonces.consume(expired, "profile:campaign-1")


def test_feedback_nonce_is_bound_to_revision_and_mismatch_does_not_consume(
    repository: OptimizerRepository, web_server
) -> None:
    repository.save_recommendation(_recommendation())
    current = repository.save_recommendation(
        replace(
            _recommendation(), input_hash="new-input", proposed_value=Decimal(130),
            change_ratio=Decimal("0.3"),
        )
    )
    _make_today_visible(repository)
    _, _, page = _request(web_server, "GET", "/")
    nonce = page.split('name="csrf_nonce" value="', 1)[1].split('"', 1)[0]
    form = {
        "csrf_nonce": nonce,
        "status": "deferred",
        "actual_value": "",
        "executed_at": "",
        "note": "",
        "recommendation_revision": "1",
    }
    wrong = _request(
        web_server, "POST", "/feedback/recommendation%2F%E4%B8%80",
        body=urlencode(form),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert wrong[0] == 409
    assert nonce not in wrong[2]

    form["recommendation_revision"] = str(current.revision)
    assert _request(
        web_server, "POST", "/feedback/recommendation%2F%E4%B8%80",
        body=urlencode(form),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )[0] == 303


def test_campaign_profile_form_creates_immutable_version_and_prg(
    repository: OptimizerRepository, web_server
) -> None:
    repository.save_profile(_profile(note="旧 <b>备注</b>"))
    _, _, page = _request(web_server, "GET", "/campaigns")
    assert "旧 &lt;b&gt;备注&lt;/b&gt;" in page
    nonce = page.split('name="csrf_nonce" value="', 1)[1].split('"', 1)[0]
    form = {
        "csrf_nonce": nonce, "effective_from": "2026-09-10",
        "minimum_roas": "2.80", "minimum_gmv_growth": "0.05",
        "growth_intensity": "0.60", "max_budget_step": "0.20",
        "max_target_roas_step": "0.10", "min_orders": "3", "cooldown_days": "2",
        "evaluation_days": "7", "rollback_days": "3",
        "allowed_actions": "increase_budget,hold", "note": "新版",
    }
    status, headers, _ = _request(
        web_server, "POST", "/campaigns/campaign%2F%E4%B8%80",
        body=urlencode(form), headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert status == 303
    assert headers["Location"] == "/campaigns?saved=1"
    assert len(repository.list_profile_versions("store-1", "campaign/一")) == 2
    assert repository.get_current_profile("store-1", "campaign/一").minimum_roas == Decimal("2.80")
    assert repository.get_current_profile("store-1", "campaign/一").status is ProfileStatus.FORMAL

    assert _request(
        web_server, "POST", "/campaigns/campaign%2F%E4%B8%80",
        body=urlencode(form), headers={"Content-Type": "application/x-www-form-urlencoded"},
    )[0] == 409
    assert len(repository.list_profile_versions("store-1", "campaign/一")) == 2

    _, _, page = _request(web_server, "GET", "/campaigns")
    fresh_nonce = page.split('name="csrf_nonce" value="', 1)[1].split('"', 1)[0]
    form["csrf_nonce"] = fresh_nonce
    form["note"] = "仅备注变化"
    assert _request(
        web_server, "POST", "/campaigns/campaign%2F%E4%B8%80",
        body=urlencode(form),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )[0] == 303
    assert len(repository.list_profile_versions("store-1", "campaign/一")) == 2


def test_profile_rejects_future_date_cross_store_and_invalid_values(
    repository: OptimizerRepository, web_server
) -> None:
    repository.save_profile(_profile())
    for path, change, expected in (
        ("/campaigns/campaign%2F%E4%B8%80", {"effective_from": "2026-09-11"}, 400),
        ("/campaigns/other", {}, 404),
    ):
        _, _, page = _request(web_server, "GET", "/campaigns")
        nonce = page.split('name="csrf_nonce" value="', 1)[1].split('"', 1)[0]
        form = {
            "csrf_nonce": nonce, "effective_from": "2026-09-10",
            "minimum_roas": "2.8", "minimum_gmv_growth": "0.05", "growth_intensity": "0.5",
            "max_budget_step": "0.2", "max_target_roas_step": "0.1", "min_orders": "3",
            "cooldown_days": "2", "evaluation_days": "7", "rollback_days": "3",
            "allowed_actions": "increase_budget,hold", "note": "",
        }
        form.update(change)
        assert _request(web_server, "POST", path, body=urlencode(form), headers={"Content-Type": "application/x-www-form-urlencoded"})[0] == expected


def test_profile_status_is_derived_and_client_status_is_rejected(
    repository: OptimizerRepository, web_server
) -> None:
    repository.save_profile(_profile())
    _, _, page = _request(web_server, "GET", "/campaigns")
    nonce = page.split('name="csrf_nonce" value="', 1)[1].split('"', 1)[0]
    form = {
        "csrf_nonce": nonce,
        "effective_from": "2026-09-10",
        "minimum_roas": "",
        "minimum_gmv_growth": "0.05",
        "growth_intensity": "0.5",
        "max_budget_step": "0.2",
        "max_target_roas_step": "0.1",
        "min_orders": "3",
        "cooldown_days": "2",
        "evaluation_days": "7",
        "rollback_days": "3",
        "allowed_actions": "increase_budget,hold",
        "note": "待补底线",
    }
    status, _, _ = _request(
        web_server,
        "POST",
        "/campaigns/campaign%2F%E4%B8%80",
        body=urlencode(form),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert status == 303
    assert repository.get_current_profile(
        "store-1", "campaign/一"
    ).status is ProfileStatus.NEEDS_FLOOR

    _, _, page = _request(web_server, "GET", "/campaigns")
    form["csrf_nonce"] = page.split('name="csrf_nonce" value="', 1)[1].split('"', 1)[0]
    form["status"] = "temporary"
    assert _request(
        web_server,
        "POST",
        "/campaigns/campaign%2F%E4%B8%80",
        body=urlencode(form),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )[0] == 400


def test_campaigns_page_uses_real_union_and_shows_snapshot_metrics_and_versions(
    repository: OptimizerRepository, web_server
) -> None:
    repository.save_profile(_profile(note="版本一"))
    repository.save_profile(
        replace(_profile(note="版本一"), effective_from=date(2026, 9, 10), note="版本二")
    )
    repository.upsert_metric(
        AdMetric(
            "store-1", "campaign/一", "活动", "__ALL__", date(2026, 9, 9),
            "THB", Decimal(10), Decimal(50), 2,
            datetime(2026, 9, 10, 3, tzinfo=UTC), "test", MetricGrain.CAMPAIGN,
        )
    )
    repository.save_snapshot(
        CampaignSnapshot(
            "store-1", "snapshot-only", "快照活动", "product",
            CampaignStatus.ACTIVE, Decimal(80), Decimal("2.2"), "THB",
            datetime(2026, 9, 10, 3, tzinfo=UTC), date(2026, 9, 9),
        )
    )
    repository.save_snapshot(
        CampaignSnapshot(
            "store-1", "future-only", "未来活动", "product",
            CampaignStatus.ACTIVE, Decimal(90), Decimal("2.3"), "THB",
            datetime(2026, 9, 10, 5, tzinfo=UTC), date(2026, 9, 9),
        )
    )

    status, _, page = _request(web_server, "GET", "/campaigns")

    assert status == 200
    assert "campaign/一" in page
    assert "花费：10" in page and "成交额：50" in page
    assert "ROAS：5.0000" in page and "订单：2" in page
    assert "snapshot-only" in page
    assert "状态：active" in page
    assert "预算：80 THB" in page and "目标 ROAS：2.2" in page
    assert "版本一" in page and "版本二" in page
    assert "future-only" not in page
    snapshot_form = page.split(
        'action="/campaigns/snapshot-only"', 1
    )[1].split("</form>", 1)[0]
    assert 'name="minimum_roas" inputmode="decimal" value="2.2"' in snapshot_form
    assert 'name="growth_intensity" inputmode="decimal" value=""' in snapshot_form
    assert "最低 GMV 增长（比例，0.20 = 20%）" in page
    assert "最低 ROAS（倍率）" in page
    assert "扩量强度（比例，0.20 = 20%）" in page
    assert "预算最大步长（比例，0.20 = 20%）" in page
    assert "目标 ROAS 最大步长（比例，0.20 = 20%）" in page
    nonce = snapshot_form.split('name="csrf_nonce" value="', 1)[1].split('"', 1)[0]
    response = _request(
        web_server,
        "POST",
        "/campaigns/snapshot-only",
        body=urlencode(
            {
                "csrf_nonce": nonce,
                "effective_from": "2026-09-09",
                "minimum_roas": "2.2",
                "minimum_gmv_growth": "0.05",
                "growth_intensity": "0.5",
                "max_budget_step": "0.2",
                "max_target_roas_step": "0.1",
                "min_orders": "3",
                "cooldown_days": "2",
                "evaluation_days": "7",
                "rollback_days": "3",
                "allowed_actions": "hold",
                "note": "来自真实快照",
            }
        ),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response[0] == 303
    assert repository.get_current_profile("store-1", "snapshot-only") is not None


def test_campaigns_empty_state_does_not_invent_campaign(web_server) -> None:
    status, _, page = _request(web_server, "GET", "/campaigns")

    assert status == 200
    assert "暂无真实广告活动" in page
    assert "/campaigns/campaign-1" not in page


@pytest.mark.parametrize(
    ("status", "actual_value"),
    [(FeedbackStatus.EXECUTED.value, ""), (FeedbackStatus.MODIFIED.value, "108")],
)
def test_feedback_saves_exact_revision_and_rejects_replay(
    repository: OptimizerRepository, web_server, status: str, actual_value: str
) -> None:
    recommendation = repository.save_recommendation(_recommendation())
    repository.upsert_metric(
        AdMetric(
            "store-1", "campaign/一", "活动", "__ALL__", date(2026, 9, 9),
            "THB", Decimal(10), Decimal(50), 2,
            datetime(2026, 9, 10, tzinfo=UTC), "test", MetricGrain.CAMPAIGN,
        )
    )
    repository.record_collection_day(
        "store-1", date(2026, 9, 9), "success",
        attempted_at=datetime(2026, 9, 10, tzinfo=UTC),
    )
    _, _, page = _request(web_server, "GET", "/")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "当前值：100" in page
    assert "建议值：120" in page
    assert "风险：0.2" in page
    assert "采集状态：success" in page
    assert "最近采集时间：2026-09-10T00:00:00+00:00" in page
    assert "实际数据日：2026-09-09" in page
    assert "整体数据健康：degraded" in page
    assert "规则版本：v1" in page
    assert "生成时间：2026-09-10T00:00:00+00:00" in page
    assert "建议数据日：2026-09-09" in page
    assert "优先级：1" in page
    assert "预期效果：0.1" in page
    assert "短窗：2026-09-03 至 2026-09-09" in page
    assert "长窗：2026-09-03 至 2026-09-09" in page
    assert "质量：degraded" in page
    assert "质量评估时间：2026-09-10T00:00:00+00:00" in page
    assert "质量说明：需补充目标" in page
    assert "缺失：target_roas" in page
    assert "规则输入：floor=2.5" in page
    assert "成功观察：3 天" in page
    nonce = page.split('name="csrf_nonce" value="', 1)[1].split('"', 1)[0]
    form = urlencode({
        "csrf_nonce": nonce, "status": status, "actual_value": actual_value,
        "executed_at": "2026-09-10T09:15", "note": "人工 <确认>",
        "recommendation_revision": str(recommendation.revision),
    })
    path = "/feedback/recommendation%2F%E4%B8%80"
    response = _request(web_server, "POST", path, body=form, headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert response[0] == 303
    saved = repository.list_feedback("store-1", recommendation.recommendation_id)
    assert len(saved) == 1
    assert saved[0].recommendation_revision == recommendation.revision
    assert saved[0].actual_value == (Decimal(108) if actual_value else None)
    assert _request(web_server, "POST", path, body=form, headers={"Content-Type": "application/x-www-form-urlencoded"})[0] == 409


@pytest.mark.parametrize("status", ["deferred", "rejected"])
def test_non_execution_feedback_uses_request_time_without_actual_value(
    repository: OptimizerRepository, web_server, status: str
) -> None:
    recommendation = repository.save_recommendation(_recommendation())
    _make_today_visible(repository)
    _, _, page = _request(web_server, "GET", "/")
    nonce = page.split('name="csrf_nonce" value="', 1)[1].split('"', 1)[0]
    response = _request(
        web_server,
        "POST",
        "/feedback/recommendation%2F%E4%B8%80",
        body=urlencode(
            {
                "csrf_nonce": nonce,
                "status": status,
                "actual_value": "",
                "executed_at": "",
                "note": "稍后处理",
                "recommendation_revision": str(recommendation.revision),
            }
        ),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response[0] == 303
    saved = repository.list_feedback("store-1", recommendation.recommendation_id)[0]
    assert saved.executed_at == datetime(2026, 9, 10, 4, tzinfo=UTC)
    assert saved.actual_value is None
    _, _, review = _request(web_server, "GET", "/reviews")
    assert "反馈记录时间：2026-09-10T04:00:00+00:00" in review
    assert "执行时间：2026-09-10T04:00:00+00:00" not in review


def test_feedback_page_separates_execution_and_non_execution_fields(
    repository: OptimizerRepository, web_server
) -> None:
    repository.save_recommendation(_recommendation())
    _make_today_visible(repository)

    _, _, page = _request(web_server, "GET", "/")

    execution = page.split('<legend>记录执行结果</legend>', 1)[1].split(
        "</fieldset>", 1
    )[0]
    non_execution = page.split('<legend>记录非执行反馈</legend>', 1)[1].split(
        "</fieldset>", 1
    )[0]
    assert 'name="actual_value"' in execution
    assert 'name="executed_at"' in execution
    assert 'name="actual_value"' not in non_execution
    assert 'name="executed_at"' not in non_execution


@pytest.mark.parametrize(
    ("status", "actual_value", "executed_at"),
    [
        ("modified", "", "2026-09-10T11:00"),
        ("executed", "1", "2026-09-10T11:00"),
        ("deferred", "1", ""),
        ("executed", "", "2026-09-10T13:00"),
        ("deferred", "", "2026-09-10T13:00"),
        ("deferred", "", "2026-09-10T11:00"),
    ],
)
def test_feedback_rejects_invalid_value_and_time_semantics(
    repository: OptimizerRepository,
    web_server,
    status: str,
    actual_value: str,
    executed_at: str,
) -> None:
    recommendation = repository.save_recommendation(_recommendation())
    _make_today_visible(repository)
    _, _, page = _request(web_server, "GET", "/")
    nonce = page.split('name="csrf_nonce" value="', 1)[1].split('"', 1)[0]
    response = _request(
        web_server,
        "POST",
        "/feedback/recommendation%2F%E4%B8%80",
        body=urlencode(
            {
                "csrf_nonce": nonce,
                "status": status,
                "actual_value": actual_value,
                "executed_at": executed_at,
                "note": "",
                "recommendation_revision": str(recommendation.revision),
            }
        ),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response[0] == 400
    assert repository.list_feedback("store-1", recommendation.recommendation_id) == ()


def test_reviews_show_exact_revision_and_only_latest_evaluation(
    repository: OptimizerRepository, web_server
) -> None:
    recommendation = repository.save_recommendation(_recommendation())
    newer = repository.save_recommendation(
        replace(
            _recommendation(),
            proposed_value=Decimal(130),
            change_ratio=Decimal("0.3"),
            input_hash="input-2",
        )
    )
    assert newer.revision == 2
    feedback = Feedback(
        "feedback-1", recommendation.recommendation_id, FeedbackStatus.EXECUTED,
        None, datetime(2026, 9, 10, 1, tzinfo=UTC), "备注 <危险>", recommendation.revision,
    )
    repository.save_feedback("store-1", feedback)
    baseline_window = WindowStats(
        date(2026, 9, 3), date(2026, 9, 5), Decimal(10), Decimal(50), 2
    )
    observation_window = WindowStats(
        date(2026, 9, 6), date(2026, 9, 9), Decimal(10), Decimal(50), 2
    )
    base = {
        "recommendation_id": recommendation.recommendation_id,
        "baseline_start": date(2026, 9, 3),
        "baseline_end": date(2026, 9, 5),
        "observation_start": date(2026, 9, 6),
        "observation_end": date(2026, 9, 9),
        "algorithm_version": "v1",
        "baseline_stats": baseline_window,
        "after_stats": observation_window,
        "recommendation_revision": recommendation.revision,
        "feedback_id": feedback.feedback_id,
        "applied_value": recommendation.proposed_value,
        "input_dates": (date(2026, 9, 9),),
    }
    repository.save_evaluation(
        "store-1", Evaluation("evaluation-1", status=EvaluationStatus.PENDING,
        evaluated_at=datetime(2026, 9, 10, 2, tzinfo=UTC), input_hash="one", **base)
    )
    repository.save_evaluation(
        "store-1", Evaluation("evaluation-2", status=EvaluationStatus.INSUFFICIENT_DATA,
        evaluated_at=datetime(2026, 9, 10, 3, tzinfo=UTC), input_hash="two", **base)
    )
    repository.save_evaluation(
        "store-1", Evaluation("evaluation-future", status=EvaluationStatus.EFFECTIVE,
        evaluated_at=datetime(2026, 9, 10, 5, tzinfo=UTC), input_hash="future", **base)
    )

    status, _, page = _request(web_server, "GET", "/reviews")
    assert status == 200
    assert "备注 &lt;危险&gt;" in page
    assert "建议修订：1" in page
    assert "建议值：120" in page
    assert "建议值：130" not in page
    assert "insufficient_data" in page
    assert "评估修订：2" in page
    assert "pending" not in page
    assert "失败" not in page
    assert "采纳率：1/2" in page
    assert "已反馈：1" in page
    assert "有效率：0/0（无已完成评估）" in page
    assert "回退判定：未评估" in page
    assert '<svg role="img"' in page
    assert "<title" in page and "<desc" in page
    assert "趋势摘要" in page


def test_reviews_show_helpful_trend_empty_state_without_evaluation(
    repository: OptimizerRepository, web_server
) -> None:
    recommendation = repository.save_recommendation(_recommendation())
    repository.save_feedback(
        "store-1",
        Feedback(
            "feedback-no-evaluation",
            recommendation.recommendation_id,
            FeedbackStatus.DEFERRED,
            None,
            datetime(2026, 9, 10, 1, tzinfo=UTC),
            "等待更多数据",
            recommendation.revision,
        ),
    )

    status, _, page = _request(web_server, "GET", "/reviews")

    assert status == 200
    assert "尚无效果评估" in page
    assert "观察期结束后再显示趋势" in page
    assert "实际值：—" in page
    assert "<svg" not in page
