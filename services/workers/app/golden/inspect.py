"""What a reviewer needs in order to label a harvested site (task 3.14, ADR-0020).

Labelling means deciding, for each value the scrubber substituted, whether the pipeline should
report it as the business's own. That decision needs the markup around the value -- a number in
a `tel:` link is the business's, the same number beside "site by" is the web designer's -- and
reading a whole fixture to find it is how labelling does not get done.

So this prints one line per occurrence: where it sat, and the markup either side of it.

    python -m app.golden inspect <slug>
"""

import re
from dataclasses import dataclass
from pathlib import Path

from app.golden.corpus import CORPUS, Labels, pages_of

#: Enough either side to see a `tel:` href or a "site by" credit, and not enough to print a page.
_WINDOW = 110


@dataclass(frozen=True, slots=True)
class Occurrence:
    page: str
    value: str
    before: str
    after: str


def occurrences(site: Labels, root: Path = CORPUS) -> list[Occurrence]:
    """Every place a labelled value appears, with the markup around it.

    All three categories, `also_theirs` included. Leaving it out made the labelling loop lossy:
    `propose --write` moves a value into `also_theirs`, the next `propose` could not see it, and
    the next `--write` deleted it -- a scrubber-written value in no category at all. The leak
    gate caught it, which is what the gate is for, but the cause was here.
    """
    wanted = sorted(
        {*site.expect.values(), *site.also_theirs, *site.absent, *site.decided},
        key=len,
        reverse=True,
    )
    found: list[Occurrence] = []
    for page, markup in pages_of(site, root):
        for value in wanted:
            for match in re.finditer(re.escape(value), markup):
                start, end = match.span()
                found.append(
                    Occurrence(
                        page=page.role,
                        value=value,
                        before=_tidy(markup[max(0, start - _WINDOW) : start]),
                        after=_tidy(markup[end : end + _WINDOW]),
                    )
                )
    return found


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def render(site: Labels, root: Path = CORPUS) -> str:
    lines = [f"{site.slug}  ({'draft' if site.draft else 'reviewed'})", ""]
    lines.append("expect:")
    for field, value in sorted(site.expect.items()):
        lines.append(f"    {field:8} {value}")
    lines.append(f"absent: {len(site.absent)} value(s)")
    lines.append("")

    seen: set[str] = set()
    for occurrence in occurrences(site, root):
        verdict = next(
            (f"EXPECT {f}" for f, v in site.expect.items() if v == occurrence.value),
            "absent",
        )
        key = f"{occurrence.page}:{occurrence.value}"
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"[{verdict:13}] {occurrence.value}   ({occurrence.page})")
        lines.append(f"    ...{occurrence.before}")
        lines.append(f"       ^^^ {occurrence.after}...")
    return "\n".join(lines)
