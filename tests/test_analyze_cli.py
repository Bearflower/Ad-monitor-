from adwatch.cli import main


def test_analyze_command_prints_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ADWATCH_DATA_DIR", str(tmp_path))
    assert main(["init"]) == 0
    assert main(["collect", "--mode", "mock", "--date", "2026-07-22"]) == 0
    assert main(["seed-business-data", "--date", "2026-07-22"]) == 0
    assert main(["analyze", "--date", "2026-07-22"]) == 0
    assert "profit_results=8" in capsys.readouterr().out


def test_analyze_range_writes_default_difference_report(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("ADWATCH_DATA_DIR", str(tmp_path))
    assert main(["init"]) == 0
    assert main(["collect", "--mode", "mock", "--date", "2026-07-22"]) == 0
    assert main(["seed-business-data", "--date", "2026-07-22"]) == 0

    assert main(
        ["analyze", "--from", "2026-07-20", "--to", "2026-07-23"]
    ) == 0

    output = capsys.readouterr().out
    report = (
        tmp_path
        / "reports"
        / "profit-recalculation-2026-07-20-2026-07-23.json"
    )
    assert report.is_file()
    assert "days=1" in output
    assert "profit_added=8" in output
    assert f"report={report}" in output


def test_analyze_rejects_incomplete_reversed_or_mixed_range(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("ADWATCH_DATA_DIR", str(tmp_path))

    assert main(["analyze", "--from", "2026-07-20"]) == 2
    assert "both --from and --to" in capsys.readouterr().out
    assert main(
        ["analyze", "--from", "2026-07-23", "--to", "2026-07-20"]
    ) == 2
    assert "must not be after" in capsys.readouterr().out
    assert main(
        [
            "analyze",
            "--date",
            "2026-07-22",
            "--from",
            "2026-07-20",
            "--to",
            "2026-07-23",
        ]
    ) == 2
    assert "either --date or --from/--to" in capsys.readouterr().out
