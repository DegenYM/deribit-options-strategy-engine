from deribit_engine.cli import _build_parser, format_command_catalog, format_nested_command_help, main


def test_format_command_catalog_lists_core_commands() -> None:
    parser = _build_parser()
    text = format_command_catalog(parser)
    assert "spot-restore" in text
    assert "profit-sweep" in text
    assert "investor" in text
    assert "Covered call ops" in text
    assert "Usage:" in text


def test_format_nested_investor_help() -> None:
    parser = _build_parser()
    text = format_nested_command_help(parser, "investor")
    assert text is not None
    assert "init" in text
    assert "validate" in text
    assert "Subcommands:" in text


def test_bot_help_command_exits_zero(capsys) -> None:
    assert main(["help"]) == 0
    out = capsys.readouterr().out
    assert "spot-restore" in out
    assert "fee-status" in out


def test_bot_help_unknown_command(capsys) -> None:
    assert main(["help", "no-such-command"]) == 2
    err = capsys.readouterr().err
    assert "Unknown command" in err


def test_bare_bot_prints_catalog(capsys) -> None:
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "Deribit options strategy bot" in out
    assert "manage" in out
