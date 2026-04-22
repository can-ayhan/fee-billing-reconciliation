"""Command line entry point: ``python -m feerecon <command>``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAW_DIR = Path("data/raw")


def cmd_generate(args: argparse.Namespace) -> int:
    from feerecon.generate import generate

    for name, rows in generate(args.out, seed=args.seed).items():
        print(f"{rows:>7,} rows  {args.out / name}")
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    from feerecon.db import connect
    from feerecon.load import load_inputs

    with connect() as conn:
        for table, rows in load_inputs(conn, args.raw).items():
            print(f"{rows:>7,} rows  {table}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    from feerecon.db import connect
    from feerecon.pipeline import build

    with connect() as conn:
        build(conn)
    print("validated, repriced and reconciled")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from feerecon.db import connect
    from feerecon.report import print_report

    with connect() as conn:
        print_report(conn)
    return 0


def cmd_excel(args: argparse.Namespace) -> int:
    from feerecon.db import connect
    from feerecon.workbook import build_workbook

    with connect() as conn:
        for sheet, rows in build_workbook(conn, args.out).items():
            print(f"{rows:>5,} rows  {sheet}")
    print(f"workbook written to {args.out}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Load, build and report in one go."""
    for step in (cmd_load, cmd_build, cmd_report):
        step(args)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="feerecon", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("generate", help="write the synthetic input files")
    p.add_argument("--out", type=Path, default=RAW_DIR)
    p.add_argument("--seed", type=int, default=2718)
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("load", help="land the input files in staging")
    p.add_argument("--raw", type=Path, default=RAW_DIR)
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("build", help="validate, clean, reprice and reconcile")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("report", help="print what the control found")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("excel", help="write the exception workbook")
    p.add_argument("--out", type=Path, default=Path("reports/fee_billing_exceptions.xlsx"))
    p.set_defaults(func=cmd_excel)

    p = sub.add_parser("run", help="load + build + report")
    p.add_argument("--raw", type=Path, default=RAW_DIR)
    p.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
