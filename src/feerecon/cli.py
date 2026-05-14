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


def cmd_extract(args: argparse.Namespace) -> int:
    from feerecon.db import connect
    from feerecon.extract import review
    from feerecon.extract.agent import extract_tariff

    text = args.document.read_text(encoding="utf-8")
    with connect() as conn:
        result = extract_tariff(text, review.known_services(conn))
        counts = review.queue(conn, args.document.name, result)
        conn.commit()
    print(f"{len(result.rows)} rows after {result.attempts} attempt(s): "
          f"{counts['approved']} approved automatically, {counts['pending']} waiting for review")
    for problem in result.problems:
        print(f"  {'problem' if problem.blocking else 'note':<8} {problem.message}")
    if result.notes:
        print(f"  notes    {result.notes}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    from feerecon.db import connect
    from feerecon.extract import review

    with connect() as conn:
        if args.action == "list":
            document = args.target[0] if args.target else None
            for row in review.listing(conn, document, only_pending=not args.all):
                review_id, doc, status, service, tier_from, tier_to, rate, unit, start, confidence, reason, quote = row
                band = f"{tier_from:f} - {'' if tier_to is None else f'{tier_to:f}'}"
                print(f"{review_id:>5}  {status:<9} {service:<8} {band:<24} {rate:f} {unit:<10} from {start}  "
                      f"conf {confidence:.2f}  {doc}")
                if reason:
                    print(f"       why: {reason}")
                print(f"       quote: {quote}")
        elif args.action in ("approve", "reject"):
            if not args.by:
                raise SystemExit("--by <your name> is required to approve or reject")
            changed = review.decide(conn, [int(t) for t in args.target], args.action == "approve", args.by)
            print(f"{changed} rows {args.action}d by {args.by}")
        else:
            if len(args.target) != 1:
                raise SystemExit("publish takes exactly one document name")
            try:
                added = review.publish(conn, args.target[0])
            except review.ReviewError as exc:
                raise SystemExit(f"not published: {exc}") from exc
            print(f"{added} rows added to pricing.fee_schedule; run `feerecon build` to revalidate and reprice")
        conn.commit()
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

    p = sub.add_parser("extract", help="read a fee schedule document into the review queue (uses the Groq API)")
    p.add_argument("document", type=Path)
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("review", help="list, approve, reject or publish extracted tariff rows")
    p.add_argument("action", choices=["list", "approve", "reject", "publish"])
    p.add_argument("target", nargs="*", help="row ids for approve/reject, document name for list/publish")
    p.add_argument("--by", help="who is deciding (approve/reject)")
    p.add_argument("--all", action="store_true", help="list rows in every status, not only pending")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("run", help="load + build + report")
    p.add_argument("--raw", type=Path, default=RAW_DIR)
    p.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    from feerecon.config import ConfigError

    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
