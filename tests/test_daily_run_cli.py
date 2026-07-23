from adwatch.cli import main


def test_daily_run_creates_all_local_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("ADWATCH_DATA_DIR", str(tmp_path))
    assert (
        main(["run", "daily", "--mode", "mock", "--date", "2026-07-22"])
        == 0
    )
    assert (tmp_path / "adwatch.sqlite3").exists()
    assert (tmp_path / "reports" / "quality-2026-07-22.json").exists()
    assert (tmp_path / "reports" / "daily-2026-07-22.md").exists()
