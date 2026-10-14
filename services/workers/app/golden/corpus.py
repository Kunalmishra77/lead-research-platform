"""The golden corpus on disk: what a fixture is, and what its labels mean (task 3.14, ADR-0020).

One directory per site under `tests/golden/corpus/<slug>/`:

    homepage.html      the scrubbed page, as the extractors will read it
    contact.html       the pages the crawler would have chosen, where it found any
    labels.json        what the pipeline should and should not end up with

`labels.json` is the whole point. Its `expect` is what a correct extraction produces after
`merge_pages` has picked the best evidence per field; its `absent` lists values that are on the
page and are not the business's -- a web designer's number in the footer, a testimonial's phone,
a platform's support address. Reporting one of those is a false positive and the report says so,
which is the half of quality that a "did we find an email" count cannot see.

Both lists are written by the scrubber's substitutions plus a human decision about each one
(ADR-0020). Nothing here is produced by running the extractors: an expectation defined as
whatever the code does today is a test that can never fail.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

#: Where the corpus lives. Beside the tests, because it is test data and has to travel with
#: them; in the repository, because a quality gate only one machine can run is not a gate.
CORPUS: Final[Path] = Path(__file__).resolve().parents[2] / "tests" / "golden" / "corpus"

LABELS: Final[str] = "labels.json"


@dataclass(slots=True)
class Page:
    """One scrubbed page of a site."""

    role: str
    file: str
    #: The URL with the host replaced, so the corpus names no real site. The path is kept: the
    #: page-choice rules read it (`/contact`, `/about`), and a corpus of `/page1` would not
    #: exercise them.
    url: str


@dataclass(slots=True)
class Labels:
    """What a correct extraction of one site produces."""

    slug: str
    harvested_at: str
    pages: list[Page]
    #: field -> the value the pipeline should hold after merging every page.
    expect: dict[str, str] = field(default_factory=dict)
    #: Values on the pages that belong to somebody else, or are not contact details at all.
    #: Finding one is a false positive.
    absent: list[str] = field(default_factory=list)
    #: Values that *are* the business's but are not the one `expect` names -- a second number in
    #: the footer when the expected one came from JSON-LD. Reporting one of these is not a
    #: mistake, it is a different correct answer, and counting it as a false positive would make
    #: the report punish a correct extraction.
    #:
    #: Tracked separately rather than merged into `expect`, because which one wins is a
    #: deliberate choice -- `merge_pages` prefers structured data over footer text -- and the
    #: report should show it when that changes rather than hide it.
    also_theirs: list[str] = field(default_factory=list)
    #: Why each `absent` entry is absent, so the next person does not "fix" the label.
    absent_because: dict[str, str] = field(default_factory=dict)
    #: A human's call on a value no rule could name, as value -> reason. It **outranks** every
    #: rule in `app/golden/propose.py` and survives a re-proposal, which is the point: a
    #: proposal is a machine reading markup, and what makes a label a label is somebody agreeing
    #: with it (ADR-0020). Without this, the residue had nowhere to go -- `propose --write` is
    #: all-or-nothing, so one unnameable value per site blocked the whole site, and the only
    #: alternative was hand-editing JSON, which has already dropped a value once.
    decided: dict[str, str] = field(default_factory=dict)
    #: Set while a site is harvested and not yet reviewed. The report refuses to score these
    #: rather than counting a draft as ground truth.
    draft: bool = True

    def to_json(self) -> str:
        return (
            json.dumps(
                {
                    "slug": self.slug,
                    "harvested_at": self.harvested_at,
                    "draft": self.draft,
                    "pages": [{"role": p.role, "file": p.file, "url": p.url} for p in self.pages],
                    "expect": self.expect,
                    "also_theirs": self.also_theirs,
                    "absent": self.absent,
                    "absent_because": self.absent_because,
                    "decided": self.decided,
                },
                indent=2,
                sort_keys=False,
            )
            + "\n"
        )

    @staticmethod
    def from_json(raw: dict[str, Any]) -> "Labels":
        return Labels(
            slug=str(raw["slug"]),
            harvested_at=str(raw.get("harvested_at", "")),
            pages=[
                Page(role=str(p["role"]), file=str(p["file"]), url=str(p["url"]))
                for p in raw.get("pages", [])
            ],
            expect={str(k): str(v) for k, v in (raw.get("expect") or {}).items()},
            also_theirs=[str(v) for v in (raw.get("also_theirs") or [])],
            absent=[str(v) for v in (raw.get("absent") or [])],
            absent_because={
                str(k): str(v) for k, v in (raw.get("absent_because") or {}).items()
            },
            decided={str(k): str(v) for k, v in (raw.get("decided") or {}).items()},
            draft=bool(raw.get("draft", False)),
        )


def load(root: Path = CORPUS) -> list[Labels]:
    """Every site in the corpus, drafts included -- the report decides what to do with those."""
    if not root.is_dir():
        return []
    sites: list[Labels] = []
    for labels in sorted(root.glob(f"*/{LABELS}")):
        sites.append(Labels.from_json(json.loads(labels.read_text(encoding="utf-8"))))
    return sites


def pages_of(site: Labels, root: Path = CORPUS) -> list[tuple[Page, str]]:
    """Each page's label and its scrubbed markup."""
    out: list[tuple[Page, str]] = []
    for page in site.pages:
        path = root / site.slug / page.file
        if path.is_file():
            out.append((page, path.read_text(encoding="utf-8")))
    return out
