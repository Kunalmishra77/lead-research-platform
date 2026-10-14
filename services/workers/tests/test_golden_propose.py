"""The proposal step (task 3.14, ADR-0020).

`propose` reads a site's labels and the markup around each value, and suggests a split. It does
not decide anything: `review` is where a human agrees, and until then the site is a draft.

The property worth testing mechanically is that the loop is stable. It was not: `occurrences`
enumerated `expect` and `absent` and not `also_theirs`, so a value promoted into `also_theirs`
by one `--write` was invisible to the next `propose` and deleted by the next `--write`. A
scrubber-written value in no category is the exact hole `test_golden_corpus_is_publishable.py`
exists to find, and it found it -- but a loop that loses labels on the second pass needs its own
test, because the gate only says that something is wrong, not that this is why.
"""

import json
from pathlib import Path

from app.golden.corpus import Labels, load
from app.golden.propose import propose, write

FIXTURE = """<html><body>
<a href="mailto:info@clinic-7.in">Email</a>
<a href="tel:+91 98765 43210">Call</a>
<p>Our other line is 0124 456 7890.</p>
<link href="https://cdn.example/a.css?ver=1794677085" rel="stylesheet">
</body></html>"""


def _site(root: Path) -> Labels:
    """A one-page corpus whose labels name every value, the way the harvester leaves them."""
    (root / "clinic").mkdir(parents=True)
    (root / "clinic" / "homepage.html").write_text(FIXTURE, encoding="utf-8")
    (root / "clinic" / "labels.json").write_text(
        json.dumps(
            {
                "slug": "clinic",
                "domain": "clinic-7.in",
                "pages": [{"role": "homepage", "file": "homepage.html", "url": "https://x/"}],
                "expect": {},
                "also_theirs": [],
                "absent": [
                    "info@clinic-7.in",
                    "+91 98765 43210",
                    "0124 456 7890",
                    "1794677085",
                ],
                "absent_because": {},
                "draft": True,
            }
        ),
        encoding="utf-8",
    )
    [site] = load(root)
    return site


def test_a_second_write_changes_nothing(tmp_path: Path) -> None:
    """Idempotence, which is the whole bug: run it twice and the labels must be identical."""
    site = _site(tmp_path)
    assert write(site, tmp_path) == []
    first = (tmp_path / "clinic" / "labels.json").read_text(encoding="utf-8")

    [reloaded] = load(tmp_path)
    assert write(reloaded, tmp_path) == []
    assert (tmp_path / "clinic" / "labels.json").read_text(encoding="utf-8") == first


def test_every_value_keeps_a_category(tmp_path: Path) -> None:
    """Nothing may fall out of the labels, however many times the loop runs."""
    site = _site(tmp_path)
    write(site, tmp_path)
    [reloaded] = load(tmp_path)
    write(reloaded, tmp_path)

    raw = json.loads((tmp_path / "clinic" / "labels.json").read_text(encoding="utf-8"))
    accounted = {
        *raw["expect"].values(),
        *raw["also_theirs"],
        *raw["absent"],
    }
    for value in ("info@clinic-7.in", "+91 98765 43210", "0124 456 7890", "1794677085"):
        assert value in accounted, value


def test_a_cache_buster_is_not_a_contact(tmp_path: Path) -> None:
    """`1794677085` is a `?ver=` timestamp that `to_e164` accepts as a Chandigarh landline, and
    it reached `also_theirs` until identity was decided before dialability."""
    site = _site(tmp_path)
    proposal = propose(site, tmp_path)
    assert "1794677085" in proposal.absent
    assert "1794677085" not in proposal.also_theirs
    assert proposal.absent_because["1794677085"]


def test_the_linked_number_is_preferred_over_the_one_in_prose(tmp_path: Path) -> None:
    """A `tel:` link is somebody pointing at their own number; a line in a paragraph is weaker
    evidence, and the pipeline weighs them the same way (`CONFIDENCE_LINKED`)."""
    site = _site(tmp_path)
    proposal = propose(site, tmp_path)
    assert proposal.expect["phone"] == "+91 98765 43210"
    assert "0124 456 7890" in proposal.also_theirs


UNDECIDABLE = """<html><body>
<a href="mailto:info@clinic-7.in">Email</a>
<p>Reference 55512345 for your appointment.</p>
</body></html>"""


def test_a_partial_labelling_is_refused_whole(tmp_path: Path) -> None:
    """The loss class, and the reason `write` is all-or-nothing.

    Writing the decided values and leaving the rest out seemed safe, because the leak gate
    fails on a value in no category. It does fail -- but the dropped value is also gone from
    `labels.json`, so the next `propose` cannot see it and the next `--write` cannot restore it.
    Two sites lost a value that way before this was fixed, and only a re-harvest brought them
    back.
    """
    (tmp_path / "clinic").mkdir(parents=True)
    (tmp_path / "clinic" / "homepage.html").write_text(UNDECIDABLE, encoding="utf-8")
    labels = tmp_path / "clinic" / "labels.json"
    labels.write_text(
        json.dumps(
            {
                "slug": "clinic",
                "domain": "clinic-7.in",
                "pages": [{"role": "homepage", "file": "homepage.html", "url": "https://x/"}],
                "expect": {},
                "also_theirs": [],
                # An eight-digit reference in prose: dialable to nobody, named by nothing.
                "absent": ["info@clinic-7.in", "55512345"],
                "absent_because": {},
                "draft": True,
            }
        ),
        encoding="utf-8",
    )
    before = labels.read_text(encoding="utf-8")

    [site] = load(tmp_path)
    assert write(site, tmp_path) == ["55512345"], "it has to say what it could not place"
    assert labels.read_text(encoding="utf-8") == before, "and it must not have written anything"
