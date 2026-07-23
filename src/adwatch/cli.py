import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="adwatch")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("init")
    collect = subcommands.add_parser("collect")
    collect.add_argument("--mode", choices=("mock", "ziniao"), default="mock")
    subcommands.add_parser("doctor")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    if args.command is None:
        parser.print_help()
    return 0


def entrypoint() -> None:
    raise SystemExit(main())
