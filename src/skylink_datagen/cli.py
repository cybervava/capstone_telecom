"""skylink-datagen command line: `generate` (data pack) and `knowledge` (knowledge pack)."""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from pathlib import Path
from typing import NoReturn

from skylink_datagen.config import ConfigError, DefectRates, GenerateConfig
from skylink_datagen.contacts import ContactPlanError
from skylink_datagen.knowledge import write_knowledge
from skylink_datagen.pipeline import run_generate

EXIT_OK, EXIT_FAILURE, EXIT_USAGE = 0, 1, 2


class UsageError(Exception):
    """Invalid command-line usage."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


def _rate(text: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from exc
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise argparse.ArgumentTypeError(f"rate must be between 0 and 1, got {text}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="skylink-datagen", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    gen = sub.add_parser("generate", help="write the synthetic data pack")
    gen.add_argument("--out", type=Path, default=Path("data/generated"))
    gen.add_argument("--customers", type=int, default=400_000)
    gen.add_argument("--seed", type=int, default=42)
    gen.add_argument("--missing-rate", type=_rate, default=0.015)
    gen.add_argument("--ambiguous-rate", type=_rate, default=0.010)
    gen.add_argument("--stale-rate", type=_rate, default=0.007)
    know = sub.add_parser("knowledge", help="write router models, guides and the hostile doc")
    know.add_argument("--out", type=Path, default=Path("data/knowledge"))
    know.add_argument("--seed", type=int, default=42)
    return parser


def _generate(args: argparse.Namespace) -> int:
    rates = DefectRates(args.missing_rate, args.ambiguous_rate, args.stale_rate)
    cfg = GenerateConfig(args.out, args.customers, args.seed, rates)
    started = time.monotonic()
    manifest = run_generate(cfg)
    elapsed = time.monotonic() - started
    for name, entry in manifest["files"].items():
        sys.stdout.write(f"{name}: {entry['rows']} rows\n")
    sys.stdout.write(f"wrote {cfg.out} in {elapsed:.1f}s\n")
    return EXIT_OK


def _knowledge(args: argparse.Namespace) -> int:
    written = write_knowledge(args.out, args.seed)
    sys.stdout.write(f"wrote {len(written)} files under {args.out}\n")
    return EXIT_OK


def _parse(argv: list[str] | None) -> argparse.Namespace | int:
    try:
        return build_parser().parse_args(argv)
    except UsageError as exc:
        sys.stderr.write(f"skylink-datagen: error: {exc}\n")
        return EXIT_USAGE
    except SystemExit as exc:  # --help / --version
        return exc.code if isinstance(exc.code, int) else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stderr)
    args = _parse(argv)
    if isinstance(args, int):
        return args
    try:
        return _generate(args) if args.command == "generate" else _knowledge(args)
    except ConfigError as exc:
        sys.stderr.write(f"skylink-datagen: error: {exc}\n")
        return EXIT_USAGE
    except (OSError, ContactPlanError) as exc:
        sys.stderr.write(f"skylink-datagen: error: {exc}\n")
        return EXIT_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
