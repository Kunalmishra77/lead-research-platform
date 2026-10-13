# ADR-0020: the golden corpus is scrubbed, and that is what makes it a label

- Status: Accepted
- Date: 2026-10-13

## Context

Task 3.14 asks for a golden dataset of 50 saved sites and a quality report, so that a change to
the extractors is a number that moved rather than an opinion. Two problems stand in the way of
the obvious implementation — save 50 pages, write down what is on them — and the second is the
interesting one.

**The repository is public.** Surveyed on 2026-10-13 over 22 real homepages drawn from the live
graph:

| | |
| --- | --- |
| companies with a domain to draw from | 495 |
| homepages carrying JSON-LD | 14 of 22 |
| `tel:` links | 14 · `mailto:` 8 · WhatsApp 8 · obfuscated email 1 |
| **email addresses that are not role accounts** | **14 on 22 homepages** |

Those fourteen are named individuals — doctors' own Gmail and Yahoo addresses, a proprietor's
personal mailbox. A verbatim corpus of fifty pages, with their contact and team pages, would
republish roughly thirty named people's addresses into a public GitHub repository. docs/10 treats
a named person's address as personal data, and `email_role` exists in this codebase precisely to
tell `info@` from `priya@`. Doing this would contradict the rule the product is built on.

Separately, a verbatim page is its owner's copyrighted content, and committing it to a public
repository is redistribution — a different objection with the same answer.

**And the labels are the real problem.** A golden dataset whose expectations are produced by
running the extractors over the pages measures nothing: the test can never fail, because the
expectation is defined as whatever the code does today. The labels have to come from somewhere
the code under test is not.

## Options considered

1. **Save the pages verbatim, label by hand.** What the task describes. Republishes thirty
   people's addresses and fifty sites' content. Rejected on the first count alone.
2. **Keep the pages out of git**, in the raw store that ADR-0019 built, and commit only labels
   and content hashes. No privacy or copyright problem, and the report can only run on the one
   host that holds the pages — so it cannot run in CI, cannot run on a laptop, and cannot be
   reviewed. A quality gate nobody else can run is not a gate.
3. **Synthesise fifty pages** in the shapes observed. No privacy problem and no corpus either: it
   would measure what I imagined the web does, which is the thing a golden dataset exists to stop
   anyone relying on.
4. **Scrub the real pages**: keep the markup and the structure, replace every contact value with
   a synthetic one of the same shape, and drop the prose. Publishable, reviewable, runnable
   anywhere — and the substitution is the label.

## Decision

**Option 4.** The corpus holds real page *structure* with synthetic contact *values*, and the
labels fall out of the scrubbing rather than being written after it.

That last part is the point, so it is worth stating plainly. When the scrubber replaces the
address in a `mailto:` href with `one@clinic-one.test`, the expectation that the extractor finds
`one@clinic-one.test` on that page comes from the substitution — not from running the extractor.
The label is independent of the code under test, which is the property option 1 was supposed to
provide and would only have provided if somebody read all fifty pages carefully.

Two rules make that sound rather than circular:

* **The scrubber matches more broadly than the extractors do.** It replaces anything that looks
  like an email or a phone number anywhere in the markup, not the values the pipeline found. A
  value the pipeline misses still gets substituted, so it still appears in the corpus and still
  appears in the labels — and the report then shows it as a miss. If the scrubber only replaced
  what the extractor found, the corpus would be both a privacy leak and blind in exactly the
  places that matter.
* **Every substitution is labelled by a human decision, not by its existence.** A page carries a
  testimonial's phone number, a web designer's credit line, a platform's support address. Those
  are substituted too, and then marked `expected: absent` — the extractor finding one is a false
  positive and the report has to say so. Deciding which is which is the hand labelling, and the
  scrubber's list of substitutions is the worklist for it.

Shape is preserved because shape is what the extractors read. `097111 74449` becomes
`098765 43210` and not `+91 98765 43210`: the national format is the thing
`app/normalize/phones.py` exists to handle, and a corpus that quietly normalises it away would
stop testing the bug that 481 numbers were dropped for. The same for a domain's TLD, a
`mailto:` versus a text address, and an obfuscated `(at)`.

Prose is dropped. A page's paragraphs are its owner's writing and none of the contact extractors
read them.

## Consequences

- The corpus is committed, reviewable and runs anywhere, including CI.
- A field the labels do not cover is not measured. Specifically **description and services
  extraction (task 3.11) cannot use this corpus**, because the prose it would read is exactly
  what was dropped. 3.11 needs its own set, and it should be built with the site owners' prose
  either licensed or synthetic — written down here so that nobody discovers it halfway through
  3.11.
- The report measures the extractors against values somebody chose. It cannot tell us that a page
  shape exists in the wild that nothing in the corpus represents; for that, the corpus has to
  keep growing from the live graph, which is why the harvester is part of this task rather than a
  one-off script.
- The synthetic domains use `.test`, which is reserved by RFC 2606 and resolves nowhere. A fixture
  cannot accidentally send mail or be mistaken for a real business, and `has_real_tld` must
  therefore accept `.test` in fixtures — which it does not, so the corpus keeps the original
  TLD on the domain and changes only the label before it. That is a deliberate compromise: a
  corpus full of `.test` would pass through a code path real pages never take.
