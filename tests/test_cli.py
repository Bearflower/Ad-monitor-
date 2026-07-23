from adwatch.cli import main


def test_cli_help_exits_successfully(capsys):
    assert main(["--help"]) == 0
    assert "collect" in capsys.readouterr().out
