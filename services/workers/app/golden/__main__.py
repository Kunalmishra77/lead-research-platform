"""`python -m app.golden` -- build and score the golden corpus (task 3.14, ADR-0020).

    python -m app.golden harvest --limit 10   # add sites from the live graph, as drafts
    python -m app.golden inspect <slug>       # every labelled value with the markup around it
    python -m app.golden review <slug>        # mark one as reviewed, so the report scores it
    python -m app.golden report               # score the reviewed ones

`harvest` needs the database and the network. `report` needs neither, which is the point: the
corpus travels with the repository so the quality gate runs anywhere, including CI.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.config import get_settings
from app.db.engine import create_engine
from app.golden.corpus import CORPUS, LABELS, load
from app.golden.harvest import harvest_many
from app.golden.inspect import render as render_inspect
from app.golden.propose import render as render_propose
from app.golden.propose import write as write_propose
from app.golden.report import render, run

#: Drawn in id order so a second harvest continues where the first stopped rather than
#: re-drawing the same sites. Platform hosts are excluded: a Wix subdomain is not a business's
#: own site, and its markup is Wix's -- the same for every tenant, so it measures nothing.
CANDIDATES = """
select c.canonical_name, c.primary_domain
  from app.companies c
 where c.primary_domain is not null
   and c.primary_domain not like '%%wixsite.com'
   and c.primary_domain not like '%%business.site'
   and c.primary_domain not like '%%blogspot.%%'
 order by c.id
 limit :limit
"""


def _in_corpus(root: Path) -> int:
    """How many sites the corpus already holds."""
    return len([p for p in root.iterdir() if (p / LABELS).exists()]) if root.is_dir() else 0


async def _harvest(limit: int, root: Path, refresh: bool = False) -> int:
    # A refresh has to see every candidate row the corpus was drawn from, not the first few, or
    # it silently rebuilds a subset. The sites already present are the limit in that mode.
    present = await asyncio.to_thread(_in_corpus, root)
    want = present if refresh else limit
    engine = create_engine(get_settings())
    async with engine.connect() as conn:
        rows = [
            (str(r[0]), str(r[1]))
            for r in (
                await conn.execute(text(CANDIDATES), {"limit": 400 if refresh else limit * 4})
            ).all()
        ]
    await engine.dispose()

    if refresh:
        print(f"{len(rows)} candidate sites; rebuilding the {present} already in the corpus")
        print("Every one goes back to DRAFT: the values are new, so the old decisions are not")
        print("decisions about them (ADR-0020).")
    else:
        print(f"{len(rows)} candidate sites; harvesting until {limit} are added")
    added = await harvest_many(rows, want=want, root=root, refresh=refresh)
    print(f"{'rebuilt' if refresh else 'added'} {len(added)}: {', '.join(added) or '(none)'}")
    print()
    print("Each is a DRAFT and the report will not score it. For each one:")
    print("    python -m app.golden inspect <slug>   # decide what belongs to the business")
    print("    # edit labels.json: expect, absent, absent_because")
    print("    python -m app.golden review <slug>")
    return 0


def _review(slug: str, root: Path) -> int:
    path = root / slug / LABELS
    if not path.is_file():
        print(f"no such site in the corpus: {slug}", file=sys.stderr)
        return 2
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not raw.get("draft", False):
        print(f"{slug} is already reviewed")
        return 0
    raw["draft"] = False
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    print(f"{slug} is now scored by the report")
    return 0


def _inspect(slug: str, root: Path) -> int:
    site = {s.slug: s for s in load(root)}.get(slug)
    if site is None:
        print(f"no such site in the corpus: {slug}", file=sys.stderr)
        return 2
    print(render_inspect(site, root))
    return 0


def _propose(slug: str, root: Path, reasons: bool = False, write: bool = False) -> int:
    site = {s.slug: s for s in load(root)}.get(slug)
    if site is None:
        print(f"no such site in the corpus: {slug}", file=sys.stderr)
        return 2
    print(render_propose(site, root, reasons=reasons))
    if write:
        left = write_propose(site, root)
        print()
        print(f"written to {slug}/{LABELS}, still a DRAFT -- `review {slug}` when you agree")
        if left:
            print(f"{len(left)} undecided value(s) went nowhere, and the leak gate will say so:")
            for value in left:
                print(f"    {value}")
            return 1
    return 0


def _report(root: Path, min_recall: float | None) -> int:
    """Prints the report and decides whether it passes.

    A false positive fails even when recall is fine: it is a value somebody was told belonged to
    a business and did not, which is worse than a value nobody was given.
    """
    report = run(root)
    print(render(report))

    if min_recall is not None:
        recall = report.total.recall
        if recall is None:
            print(f"nothing scored, so --min-recall {min_recall} cannot be met")
            return 1
        if recall < min_recall:
            print(f"recall {100 * recall:.1f}% is below the floor {100 * min_recall:.1f}%")
            return 1

    if report.total.wrong:
        print(f"{report.total.wrong} value(s) reported that the labels say are not theirs")
        return 1
    return 0


#: Each sub-command and what runs it, so `main` stays a parser and a lookup.
_COMMANDS: dict[str, Callable[[Any], int]] = {
    "harvest": lambda a: asyncio.run(_harvest(a.limit, a.path, a.refresh)),
    "inspect": lambda a: _inspect(a.slug, a.path),
    "propose": lambda a: _propose(a.slug, a.path, a.reasons, a.write),
    "review": lambda a: _review(a.slug, a.path),
    "report": lambda a: _report(a.path, a.min_recall),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.golden")
    sub = parser.add_subparsers(dest="command", required=True)

    grab = sub.add_parser("harvest", help="add sites from the live graph, as drafts")
    grab.add_argument("--limit", type=int, default=10)
    grab.add_argument("--path", type=Path, default=CORPUS)
    grab.add_argument(
        "--refresh",
        action="store_true",
        help="rebuild the sites already in the corpus instead of adding new ones, "
        "for when the scrubber -- which writes the labels -- has changed",
    )

    look = sub.add_parser("inspect", help="show each labelled value with the markup around it")
    look.add_argument("slug")
    look.add_argument("--path", type=Path, default=CORPUS)

    plan = sub.add_parser("propose", help="a proposed split, and what a human has to decide")
    plan.add_argument("slug")
    plan.add_argument("--path", type=Path, default=CORPUS)
    plan.add_argument(
        "--reasons", action="store_true", help="print why each absent value is absent"
    )
    plan.add_argument(
        "--write",
        action="store_true",
        help="record the proposal in labels.json, still as a draft (review is a separate step)",
    )

    done = sub.add_parser("review", help="mark a harvested site as reviewed")
    done.add_argument("slug")
    done.add_argument("--path", type=Path, default=CORPUS)

    score = sub.add_parser("report", help="score the reviewed sites")
    score.add_argument("--path", type=Path, default=CORPUS)
    score.add_argument(
        "--min-recall", type=float, default=None, help="exit non-zero below this, for CI"
    )

    args = parser.parse_args(argv)
    return _COMMANDS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
