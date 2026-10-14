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
from typing import Any, Final

from sqlalchemy import text

from app.config import get_settings
from app.db.engine import create_engine
from app.golden.corpus import CORPUS, LABELS, load
from app.golden.harvest import harvest_many, slug_for
from app.golden.inspect import render as render_inspect
from app.golden.propose import render as render_propose
from app.golden.propose import write as write_propose
from app.golden.report import render, run
from app.normalize.domains import site_domain

#: Drawn in id order so a second harvest continues where the first stopped rather than
#: re-drawing the same sites. The SQL keeps the three platform hosts that are cheap to spell;
#: `is_own_site` does the rest, because the interesting cases are not spellable in advance.
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

#: Hosts that are somebody else's page about a business rather than the business's own site. Each
#: one is in the live graph as a company's `primary_domain`, which is a finding in itself: a
#: customer handed `bit.ly` as a lead's website has been handed nothing. Four of 495 companies.
#:
#: They are excluded here for a narrower reason: a shortener's markup is a shortener's, and a
#: marketplace listing's markup is the marketplace's. Measuring extraction against it measures
#: Practo.
_NOT_A_BUSINESS_SITE: Final[frozenset[str]] = frozenset(
    {
        "bit.ly",
        "g.co",
        "g.page",
        "goo.gl",
        "lnkd.in",
        "t.co",
        "wa.me",
        "practo.com",
        "justdial.com",
        "sulekha.com",
        "indiamart.com",
        "facebook.com",
        "instagram.com",
        "linkedin.com",
        "youtube.com",
    }
)


def is_own_site(domain: str) -> bool:
    """Whether this domain is the business's own site, rather than a platform or a listing.

    Two tests, and the second is the one that matters.

    A subdomain of somebody else's registrable domain is a tenant of a website builder:
    `3d-the-gym.grexa.site`, `amit-fitness-training-expert.hovermedia.in`, `*.netlify.app`,
    `*.my.canva.site`. **`grexa.site` alone is 16 of the 495 companies that have a domain.** Their
    markup is the builder's template, identical for every tenant, so a corpus full of them
    measures the platform rather than the extractors -- the same reason `wixsite.com` was excluded
    by hand, now decided by the shape of the domain instead of by a list somebody has to maintain.

    `site_domain` rather than counting dots, and rather than `registrable_domain`, which returns
    the whole host because that is what `companies.primary_domain` stores. Counting dots cannot
    tell a public suffix from a platform -- `co.in` was the most common "shared host" in the
    graph for exactly that reason, and `clinic.co.in` is a business's own domain.
    """
    if domain.lower() in _NOT_A_BUSINESS_SITE:
        return False
    bare = domain.lower().removeprefix("www.")
    collapsed = site_domain(f"https://{bare}")
    return collapsed is None or collapsed == bare


def _in_corpus(root: Path) -> int:
    """How many sites the corpus already holds."""
    return len([p for p in root.iterdir() if (p / LABELS).exists()]) if root.is_dir() else 0


async def _harvest(limit: int, root: Path, refresh: bool = False, only: str = "") -> int:
    # A refresh has to see every candidate row the corpus was drawn from, not the first few, or
    # it silently rebuilds a subset. The sites already present are the limit in that mode.
    present = await asyncio.to_thread(_in_corpus, root)
    want = present if refresh else limit
    engine = create_engine(get_settings())
    async with engine.connect() as conn:
        rows = [
            (str(r[0]), str(r[1]))
            for r in (
                await conn.execute(text(CANDIDATES), {"limit": 800 if refresh else limit * 8})
            ).all()
            if is_own_site(str(r[1]))
        ]
    await engine.dispose()

    if refresh:
        print(f"{len(rows)} candidate sites; rebuilding the {present} already in the corpus")
        print("Every one goes back to DRAFT: the values are new, so the old decisions are not")
        print("decisions about them (ADR-0020).")
    else:
        print(f"{len(rows)} candidate sites; harvesting until {limit} are added")
    if only:
        # One site, for the usual case: the scrubber changed and a single fixture is out of date.
        # Re-reading forty-nine other people's servers to rebuild one of them is not polite.
        rows = [row for row in rows if slug_for(row[0], row[1]) == only]
        if not rows:
            print(f"no candidate in the graph produces the slug {only!r}")
            return 2
        want = len(rows)
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
        if not left:
            print(f"written to {slug}/{LABELS}, still a DRAFT -- `review {slug}` when you agree")
        else:
            print(f"nothing written: {len(left)} value(s) are undecided, and a partial labelling")
            print("would drop them from labels.json, where the next run could no longer see them:")
            for value in left:
                print(f"    {value}")
            return 1
    return 0


def _decide(slug: str, value: str, reason: str, root: Path) -> int:
    """Records a human's call on one value, which then outranks every rule in `propose`.

    The residue needs somewhere to go. `propose --write` is all-or-nothing, so one value no rule
    can name blocks a whole site, and the only other way to place it was editing the JSON by
    hand -- which dropped a value from a label once already. A decision recorded here survives
    every later `--write`, and the reason travels into `absent_because` where the next person
    reads it.
    """
    path = root / slug / LABELS
    if not path.is_file():
        print(f"no such site in the corpus: {slug}", file=sys.stderr)
        return 2
    if not reason.strip():
        print("a decision without a reason is not a decision", file=sys.stderr)
        return 2

    raw = json.loads(path.read_text(encoding="utf-8"))
    decided = dict(raw.get("decided") or {})
    decided[value] = reason.strip()
    raw["decided"] = decided
    raw["draft"] = True
    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{slug}: {value!r} is absent, because {reason.strip()}")
    print(f"back to DRAFT -- run `propose {slug} --write` then `review {slug}`")
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
    "harvest": lambda a: asyncio.run(_harvest(a.limit, a.path, a.refresh, a.only)),
    "inspect": lambda a: _inspect(a.slug, a.path),
    "propose": lambda a: _propose(a.slug, a.path, a.reasons, a.write),
    "review": lambda a: _review(a.slug, a.path),
    "decide": lambda a: _decide(a.slug, a.value, a.reason, a.path),
    "report": lambda a: _report(a.path, a.min_recall),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.golden")
    sub = parser.add_subparsers(dest="command", required=True)

    grab = sub.add_parser("harvest", help="add sites from the live graph, as drafts")
    grab.add_argument("--limit", type=int, default=10)
    grab.add_argument("--path", type=Path, default=CORPUS)
    grab.add_argument(
        "--only", default="", help="one slug, for when a single fixture is out of date"
    )
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

    call = sub.add_parser("decide", help="record a human's call on one value a rule cannot name")
    call.add_argument("slug")
    call.add_argument("value", help="exactly as `propose` printed it")
    call.add_argument("reason", help="why it is not the business's contact detail")
    call.add_argument("--path", type=Path, default=CORPUS)

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
