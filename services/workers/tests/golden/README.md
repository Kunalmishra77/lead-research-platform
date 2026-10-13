# The golden corpus

Real page structure, synthetic contact values, hand-written labels. Task 3.14, and the reasoning
is in [ADR-0020](../../../../docs/adr/0020-the-golden-corpus-is-scrubbed-and-that-is-what-makes-it-a-label.md).

## Why the pages are not real

This repository is public. Surveyed over 22 homepages drawn from the live graph, **14 carried a
named individual's address** — a doctor's own Gmail, a proprietor's personal mailbox. Fifty pages
with their contact and team pages would republish about thirty people's addresses, and docs/10
treats a named person's address as personal data. A verbatim page is also its owner's
copyrighted content.

So every contact value is substituted with a synthetic one of the same shape, the stylesheets,
scripts and inline SVG are stripped, and the prose is dropped.

## Why that makes it a better corpus, not a worse one

**The substitution is the label.** When the scrubber puts `info@clinic-11.in` in a `mailto:`
href, the expectation that the extractor finds `info@clinic-11.in` comes from having put it
there. It does not come from running the extractor — which is what a corpus labelled by the code
under test would do, and that corpus can never fail.

Two rules keep it honest:

- **The scrubber matches more broadly than the extractors do.** A value the pipeline misses still
  gets substituted, so it is still in the fixture, still in the labels, and shows up as a miss.
- **Every substitution gets a human decision, not a default.** A testimonial's phone, a web
  designer's credit line, a WordPress `?ver=` timestamp: all substituted, all labelled `absent`,
  and the extractor reporting one is a false positive.

## The three label categories

| | |
| --- | --- |
| `expect` | what the pipeline should hold for this field after `merge_pages` |
| `also_theirs` | genuinely the business's, but not the one `expect` names — a second number in the footer when the expected one came from JSON-LD. Reporting it is a different correct answer, not a mistake |
| `absent` | somebody else's, or not a contact detail at all. Reporting it is a false positive |

`also_theirs` exists because the first reviewed site had two real numbers and marking the loser
`absent` would have made a correct extraction count as an error.

## Working on it

```
python -m app.golden harvest --limit 10   # add sites from the live graph, as drafts
python -m app.golden inspect <slug>       # every labelled value with the markup around it
python -m app.golden review <slug>        # mark it reviewed, so the report scores it
python -m app.golden report               # score the reviewed sites
```

A harvested site is a **draft** and the report ignores it. The draft's `expect` is the
harvester's guess and it is often wrong — on the second site reviewed it had proposed a WordPress
cache-buster as the phone number. Reviewing means opening the fixture, fixing the three lists,
writing a line in `absent_because` for each absent value, and then `review`.

## What the tests enforce

- `test_golden_corpus_is_publishable.py` — every contact-shaped string in every fixture is one
  the scrubber wrote. Anything else is treated as real until proven otherwise. It has already
  caught a value dropped from a label during review.
- `test_golden_report.py` — no false positives, ever; recall does not fall; and the three label
  categories do not overlap.

## What this corpus cannot measure

Description and services extraction (task 3.11). The prose it would read is exactly what was
dropped, so 3.11 needs its own set, built from licensed or synthetic text.
