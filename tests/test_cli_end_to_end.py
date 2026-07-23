import json

from adwatch.cli import main


def test_mock_collection_writes_database_and_quality_report(tmp_path, monkeypatch):
    monkeypatch.setenv("ADWATCH_DATA_DIR", str(tmp_path))
    assert main(["init"]) == 0
    assert main(["collect", "--mode", "mock", "--date", "2026-07-22"]) == 0

    report = json.loads(
        (tmp_path / "reports" / "quality-2026-07-22.json").read_text()
    )
    assert {item["platform"] for item in report["runs"]} == {
        "tiktok",
        "shopee",
    }
    assert report["totals"]["accepted"] >= 8
    assert report["totals"]["quarantined"] == 0
