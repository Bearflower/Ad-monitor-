from adwatch.cli import main


def test_analyze_command_prints_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ADWATCH_DATA_DIR", str(tmp_path))
    assert main(["init"]) == 0
    assert main(["collect", "--mode", "mock", "--date", "2026-07-22"]) == 0
    assert main(["seed-business-data", "--date", "2026-07-22"]) == 0
    assert main(["analyze", "--date", "2026-07-22"]) == 0
    assert "profit_results=8" in capsys.readouterr().out
