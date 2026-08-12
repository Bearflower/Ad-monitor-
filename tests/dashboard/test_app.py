from datetime import date

from adwatch.analytics.service import AnalysisService
from adwatch.collectors.mock import MockCollector
from adwatch.dashboard.app import (
    _parse_evidence_multipart,
    _resolve_dashboard_date,
    render_dashboard,
)
from adwatch.domain import Platform
from adwatch.pipeline.runner import PipelineRunner
from adwatch.storage.db import Database


def test_dashboard_is_read_only_responsive_and_escapes_data(tmp_path):
    data_date = date(2026, 7, 22)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    runner = PipelineRunner(database)
    runner.run(MockCollector(Platform.TIKTOK), data_date)
    runner.run(MockCollector(Platform.SHOPEE), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    service.run(data_date)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE daily_ad_metrics SET store='<script>alert(1)</script>' "
            "WHERE platform='tiktok'"
        )

    html = render_dashboard(database, data_date, simulated=True)

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "模拟数据" in html
    assert "TikTok" in html and "Shopee" in html
    assert "净利润" in html and "策略建议" in html
    assert "数据来源与可信度" in html
    assert 'class="data-source-list"' in html
    assert "关键缺口" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "overflow-x:auto" not in html.split(
        "/* ===== Data Source Trust Cards =====", 1
    )[1].split("/* =====", 1)[0]
    assert '<meta name="viewport"' in html


def test_dashboard_filters_platform_and_exposes_filter_controls(tmp_path):
    data_date = date(2026, 7, 22)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    runner = PipelineRunner(database)
    runner.run(MockCollector(Platform.TIKTOK), data_date)
    runner.run(MockCollector(Platform.SHOPEE), data_date)

    html = render_dashboard(
        database,
        data_date,
        simulated=True,
        platform="shopee",
    )

    assert 'name="platform"' in html
    assert "Shopee" in html
    assert 'class="eyebrow">Tiktok' not in html


def test_dashboard_renders_trends_quality_and_execution(tmp_path):
    data_date = date(2026, 7, 22)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)

    page = render_dashboard(database, data_date, simulated=False)

    assert "7 天趋势" in page
    assert "14 天趋势" in page
    assert "30 天趋势" in page
    assert "采集运行质量" in page
    assert "审批与执行状态" in page
    assert "本月经营" in page
    assert "年度经营" in page
    assert "/operating-statement?year=2026" in page
    assert "SGD → CNY" in page


def test_dashboard_default_date_tracks_latest_collected_data(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    runner = PipelineRunner(database)
    runner.run(MockCollector(Platform.SHOPEE), date(2026, 7, 22))
    runner.run(MockCollector(Platform.SHOPEE), date(2026, 7, 24))

    resolved = _resolve_dashboard_date(
        database, requested=None, fallback=date(2026, 7, 20)
    )

    assert resolved == date(2026, 7, 24)


def test_real_dashboard_formats_net_profit_as_cny(tmp_path):
    data_date = date(2026, 7, 22)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    service.run(data_date)

    page = render_dashboard(database, data_date, simulated=False)

    assert "真实数据" in page
    assert '<dt>净利润</dt><dd class="number' in page
    assert "¥" in page
    assert "<th>日期</th><th>消耗</th><th>GMV</th><th>ROAS</th><th>净利润</th>" in page


def test_evidence_multipart_parser_preserves_binary_and_rejects_duplicates():
    boundary = "adwatch-boundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="csrf_token"\r\n\r\n'
        "secret\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="target"\r\n\r\n'
        "financial_adjustment:abc\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="凭证.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode() + b"%PDF-1.7\x00binary\r\n" + f"--{boundary}--\r\n".encode()

    parsed = _parse_evidence_multipart(
        f"multipart/form-data; boundary={boundary}", body
    )

    assert parsed.fields["csrf_token"] == "secret"
    assert parsed.fields["target"] == "financial_adjustment:abc"
    assert parsed.filename == "凭证.pdf"
    assert parsed.media_type == "application/pdf"
    assert parsed.content == b"%PDF-1.7\x00binary"
