"""The golden corpus as a test (task 3.14, ADR-0020).

This is the gate: it runs the real extraction path over the corpus in the repository and fails
when a reviewed site stops producing what its labels say. No network, no database, no credentials
-- the corpus travels with the tests, which is the whole reason it is scrubbed rather than kept
on one host.

Two floors rather than one:

* **No false positives, ever.** A value reported as a business's contact when the labels say it
  belongs to a web designer or is a `?ver=` cache-buster is worse than a value nobody was given:
  somebody acts on it.
* **Recall does not fall.** The number is low and deliberately so -- it is what the corpus
  measures today, and it is a floor rather than a target. Raising it is a separate decision from
  passing the test.
"""


import pytest

from app.golden.corpus import CORPUS, load
from app.golden.report import render, run, same

#: What the corpus scored when this floor was written, rounded down. A change that improves
#: extraction should raise it in the same commit; a change that lowers it has to say why.
#: A ratchet, not an aspiration. It was 1.0 when the corpus was two sites chosen by hand, which
#: is a floor that only holds while the corpus is small enough to be lucky. At ten sites the
#: measured figure is 92.9%, and the one gap is understood: `drpearldentalstudio` prints its
#: address only inside `<meta property="og:description">`, an attribute the extractor never reads,
#: because the visible copy is behind Cloudflare's email obfuscation.
#:
#: So this sits just under the measured value: any regression trips it, and the number goes up
#: when the extractors improve. Raising it is the point -- it is the one place the corpus says out
#: loud how good the pipeline currently is. It has gone 1.0 (two sites, chosen) -> 0.92 (ten
#: sites, measured) -> 0.96 (twenty-five sites, after the corpus found two real defects).
RECALL_FLOOR = 0.96


def test_the_corpus_has_sites_in_it() -> None:
    """A gate with nothing behind it passes silently, which is the failure mode of every
    quality check nobody looks at."""
    assert load(CORPUS), "no corpus; run `python -m app.golden harvest`"


def test_every_reviewed_site_still_produces_what_its_labels_say() -> None:
    report = run(CORPUS)
    if report.sites_scored == 0:
        pytest.skip("every site in the corpus is still a draft")
    recall = report.total.recall
    assert recall is not None
    assert recall >= RECALL_FLOOR, "\n" + render(report)


def test_nothing_is_reported_that_the_labels_say_is_not_the_businesss() -> None:
    """The half a recall figure cannot see.

    One of the labelled sites prints a WordPress `?ver=` timestamp that the harvester's draft
    had guessed was the phone number. If the extractor ever agrees with that guess, recall stays
    where it is and a customer gets a ten-digit cache-buster to ring.
    """
    report = run(CORPUS)
    assert report.total.wrong == 0, "\n" + render(report)


def test_a_label_file_names_pages_that_exist() -> None:
    """A fixture renamed or lost leaves a label pointing at nothing, and the site then scores
    zero for a reason that has nothing to do with extraction."""
    for site in load(CORPUS):
        for page in site.pages:
            path = CORPUS / site.slug / page.file
            assert path.is_file(), f"{site.slug}: {page.file} is missing"


def test_a_reviewed_site_explains_every_value_it_calls_absent() -> None:
    """`absent_because` is how the next person knows not to "fix" the label.

    Only on reviewed sites: a draft's reasons are the harvester's guesses and saying so is the
    point of the draft flag.
    """
    for site in load(CORPUS):
        if site.draft:
            continue
        for value in site.absent:
            assert site.absent_because.get(value), (
                f"{site.slug}: {value!r} is labelled absent with no reason given"
            )


def test_no_value_is_in_two_categories_at_once() -> None:
    """`expect`, `also_theirs` and `absent` have to disagree, or the report's verdict depends on
    which list it checks first."""
    for site in load(CORPUS):
        expected = set(site.expect.values())
        also = set(site.also_theirs)
        absent = set(site.absent)
        assert not expected & also, f"{site.slug}: in expect and also_theirs"
        assert not expected & absent, f"{site.slug}: in expect and absent"
        assert not also & absent, f"{site.slug}: in also_theirs and absent"


def test_a_phone_is_compared_by_its_digits() -> None:
    """The label says what is printed on the page and the pipeline holds E.164, so comparing
    the strings made a correct extraction read as a miss on the first site reviewed.

    Digits rather than running the normalizer on both sides: calling the code under test to
    decide whether it is right would hide the bug worth catching.

    Compared on the last ten digits, which is an Indian subscriber number. This assertion used
    to read `same("phone", "0124-940-4482", "+911249404482") is False`, on the grounds that "a
    missing country code is a different number" -- but that pair is a printed national number
    and its correct E.164 form, so the rule it encoded scored a correct extraction as a miss.
    It did: `remedium-dental-care` prints `9001268807`, the pipeline read `+919001268807`, and
    the report called it a failure.

    Whether the pipeline emits E.164 at all is a question for `app/normalize/phones.py` and its
    own tests. What the report has to answer is whether we found the right business's number.
    """
    assert same("phone", "+91-950-896-4880", "+919508964880")
    assert same("phone", "9001268807", "+919001268807"), "the same number, printed and stored"
    assert same("phone", "0124-940-4482", "+911249404482"), "a trunk zero becomes a country code"

    assert not same("phone", "+919508964880", "+919508964881"), "one digit apart is not the same"
    assert not same("phone", "0124-940-4482", "+91124940448"), "a dropped digit is not forgiven"
    assert not same("phone", "", "+919508964880")


def test_an_email_is_compared_without_case() -> None:
    assert same("email", "Info@Clinic.IN", "info@clinic.in")
    assert not same("email", "info@clinic.in", "sales@clinic.in")


def test_the_corpus_contains_no_page_that_was_never_labelled() -> None:
    """A fixture with no `labels.json` is bytes in the repository that measure nothing, and it
    is also an unreviewed scrub sitting in a public repository."""
    for directory in sorted(p for p in CORPUS.iterdir() if p.is_dir()):
        assert (directory / "labels.json").is_file(), f"{directory.name} has no labels"
        assert list(directory.glob("*.html")), f"{directory.name} has no pages"
