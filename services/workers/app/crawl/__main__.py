"""`python -m app.crawl prune` -- the raw store's retention sweep, by hand.

The worker runs it at startup (ADR-0019), which bounds the store as long as deploys keep
happening. This exists for the times they do not: a host filling up, or a sweep somebody wants
to run now rather than at the next restart.
"""

import argparse
import sys
from pathlib import Path

from app.config import get_settings
from app.crawl.rawstore import RETENTION_DAYS, prune


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.crawl")
    sub = parser.add_subparsers(dest="command", required=True)
    sweep = sub.add_parser("prune", help="delete stored pages past the retention window")
    sweep.add_argument(
        "--keep-days",
        type=int,
        default=RETENTION_DAYS,
        help=f"how long a page is kept (default {RETENTION_DAYS}, docs/04)",
    )
    sweep.add_argument("--path", type=Path, default=None, help="override RAW_STORE_PATH")
    args = parser.parse_args(argv)

    root = args.path or get_settings().RAW_STORE_PATH
    if root is None:
        print("RAW_STORE_PATH is not set and no --path given; nothing to prune", file=sys.stderr)
        return 2

    result = prune(root, older_than_days=args.keep_days)
    print(
        f"pruned {result.files} pages "
        f"({result.bytes / 1_000_000:.1f} MB), "
        f"{result.months_removed} empty month(s) removed, keeping {args.keep_days} days"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
