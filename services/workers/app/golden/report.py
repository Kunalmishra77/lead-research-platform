"""The quality report (task 3.14): what the extractors get right, per field, as a number.

Runs the real extraction path -- `values_from_page` for each page, then `merge_pages` -- over the
corpus and compares the result against labels nothing in that path produced (ADR-0020).

Three outcomes per field, and the third is the one a "did we find an email" count cannot see:

* **found**   the label's value, extracted
* **missed**  the label's value, not extracted
* **wrong**   something extracted that the label says is not the business's -- a web designer's
              number in the footer, a testimonial's phone, a platform's support address

A draft site is not scored. A label written by the harvester and not yet reviewed is a guess,
and counting guesses as ground truth is how a quality gate comes to measure nothing.
"""

from dataclasses import dataclass, field
from pathlib import Path

from app.crawl.mapping import merge_pages, values_from_page
from app.golden.corpus import CORPUS, Labels, load, pages_of

#: Fields whose value the pipeline normalises, so the label and the result are compared loosely.
_PHONE_FIELDS = frozenset({"phone", "whatsapp"})


@dataclass(slots=True)
class FieldScore:
    found: int = 0
    missed: int = 0
    wrong: int = 0
    #: Reported a value the labels say is the business's but is not the one `expect` names --
    #: the footer's number instead of the JSON-LD one. Not a mistake and not a pass: it means
    #: the preference order changed, which is worth seeing rather than scoring.
    other: int = 0

    @property
    def expected(self) -> int:
        return self.found + self.missed

    @property
    def recall(self) -> float | None:
        """None rather than 0.0 when nothing was expected: 0 of 0 is not a failure."""
        return None if self.expected == 0 else self.found / self.expected


@dataclass(slots=True)
class Report:
    sites_scored: int = 0
    sites_draft: int = 0
    fields: dict[str, FieldScore] = field(default_factory=dict)
    #: Site slug -> what went wrong there, so a failure names the page to open.
    detail: dict[str, list[str]] = field(default_factory=dict)

    def score(self, name: str) -> FieldScore:
        return self.fields.setdefault(name, FieldScore())

    @property
    def total(self) -> FieldScore:
        out = FieldScore()
        for score in self.fields.values():
            out.found += score.found
            out.missed += score.missed
            out.wrong += score.wrong
            out.other += score.other
        return out


def same(field: str, label: str, extracted: str) -> bool:
    """Whether what the pipeline produced is the value the label names.

    A phone is compared by its digits alone. The label says what is printed on the page --
    `+91-950-896-4880` -- and the pipeline holds E.164, because normalising it is the whole job
    of `app/normalize/phones.py`. Comparing the strings made a correct extraction read as a
    miss on the first site reviewed.

    Digits rather than running the normalizer on both sides: calling the code under test to
    decide whether the code under test is right would hide exactly the bug worth catching. A
    normalisation that dropped the country code changes the digits and is still caught here.
    """
    if field in _PHONE_FIELDS:
        return _digits(label) == _digits(extracted)
    return label.strip().lower() == extracted.strip().lower()


def _digits(value: str) -> str:
    return "".join(c for c in value if c.isdigit())


def _extract(site: Labels, root: Path) -> dict[str, str]:
    """What the pipeline ends up holding for this site, through the real path."""
    facts = [
        values_from_page(markup, page_url=page.url, region="IN")
        for page, markup in pages_of(site, root)
    ]
    return {value.field: str(value.value) for value in merge_pages(facts)}


def run(root: Path = CORPUS) -> Report:
    report = Report()
    for site in load(root):
        if site.draft:
            report.sites_draft += 1
            continue
        report.sites_scored += 1
        extracted = _extract(site, root)
        problems: list[str] = []

        for name, wanted in site.expect.items():
            score = report.score(name)
            got = extracted.get(name)
            if got is not None and same(name, wanted, got):
                score.found += 1
            elif got is not None and any(same(name, other, got) for other in site.also_theirs):
                # The business's, but not the one the labels prefer. Recorded and not counted
                # against recall: punishing this would mean the report demands a particular
                # preference order rather than a correct answer.
                score.other += 1
                problems.append(f"{name}: expected {wanted!r}, got {got!r}, also theirs")
            else:
                score.missed += 1
                problems.append(f"{name}: expected {wanted!r}, got {got!r}")

        # A value the label says belongs to somebody else, reported as the business's. Counted
        # against the field it appeared in, so the report points at the extractor to fix.
        for name, got in extracted.items():
            if any(same(name, other, got) for other in site.also_theirs):
                continue
            if any(same(name, bad, got) for bad in site.absent):
                report.score(name).wrong += 1
                why = site.absent_because.get(got, "labelled absent")
                problems.append(f"{name}: reported {got!r}, which is {why}")

        if problems:
            report.detail[site.slug] = problems
    return report


def render(report: Report) -> str:
    """The report as a developer reads it: one line per field, then what to go and look at."""
    lines: list[str] = []
    lines.append(f"corpus: {report.sites_scored} sites scored, {report.sites_draft} draft")
    if report.sites_scored == 0:
        lines.append("nothing to score. Review a harvested site and set `draft: false`.")
        return "\n".join(lines)

    lines.append("")
    lines.append(
        f"{'field':16} {'found':>6} {'missed':>7} {'wrong':>6} {'other':>6}  recall"
    )
    for name in sorted(report.fields):
        score = report.fields[name]
        recall = "   —" if score.recall is None else f"{100 * score.recall:5.1f}%"
        lines.append(
            f"{name:16} {score.found:6} {score.missed:7} {score.wrong:6}"
            f" {score.other:6}  {recall}"
        )
    total = report.total
    overall = "   —" if total.recall is None else f"{100 * total.recall:5.1f}%"
    lines.append(
        f"{'total':16} {total.found:6} {total.missed:7} {total.wrong:6}"
        f" {total.other:6}  {overall}"
    )

    if report.detail:
        lines.append("")
        lines.append("what to look at:")
        for slug in sorted(report.detail):
            lines.append(f"  {slug}")
            for problem in report.detail[slug]:
                lines.append(f"    {problem}")
    return "\n".join(lines)
