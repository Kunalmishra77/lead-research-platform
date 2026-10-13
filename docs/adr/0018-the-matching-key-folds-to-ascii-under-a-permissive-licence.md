# ADR-0018: the matching key folds to ASCII, under a permissive licence

- Status: Accepted
- Date: 2026-10-13

## Context

`companies.normalized_name` is what entity resolution blocks on. docs/06 section 6 specifies it:
trim, collapse whitespace, strip legal suffixes, "unidecode for matching key". Today it does the
first two and neither of the others.

Measured on the live graph (1,550 companies) before deciding anything:

| | |
| --- | --- |
| names ending in a legal suffix (`Pvt Ltd`, `LLP`, …) | 8 |
| pairs that stripping the suffix would newly match | **0** |
| names containing a non-ASCII character | 24 |
| **keys containing a non-ASCII character** | **11** |

The 11 are the ones that matter, and the worst of them are not accents:

```
𝐂𝐃𝐂 - 𝐂𝐨𝐬𝐦𝐨𝐝𝐨𝐧𝐭𝐢𝐬𝐭 𝐃𝐞𝐧𝐭𝐚𝐥 & 𝐈𝐦𝐩𝐥𝐚𝐧𝐭 𝐂𝐞𝐧𝐭𝐫𝐞
𝗛𝗢𝗟𝗜𝗦𝗧𝗜𝗖 𝗗𝗘𝗡𝗧𝗔𝗟 - DR ANU RATHI
```

Those are mathematical bold letters, not `C` and `D`. `str.isalnum()` calls them alphanumeric, so
the current normalizer keeps them verbatim and the key can never match the same business written
in plain letters — by another source, by a crawl of its own site, or by a customer's import. The
rest are accents (`café`, `jüSTa`, `Viśokam`), one Polish `Ł`, and nine names written partly or
wholly in Devanagari.

So the fix is worth doing for the folding, not for the suffixes. The question is what to fold
with.

Python's stdlib `unicodedata.normalize("NFKD", …)` handles the compatibility forms — the
mathematical letters, the full-width letters, and the accents once combining marks are dropped.
It fixes 8 of the 11. It does nothing for `Ł`, which has no decomposition, and nothing for
Devanagari.

docs/06 names `unidecode`, which handles all of them. **Unidecode is GPL.** docs/02's dependency
rule bans AGPL in closed code without approval and asks that every licence be checked; GPL is not
on the banned list, but this product is heading for a commercial launch (Phase 11) and may one day
ship an image somebody else runs, at which point a GPL library in the worker is a question
somebody has to answer under time pressure. It is cheaper to not have the question.

## Options considered

1. **NFKD only (stdlib).** No dependency at all. Fixes the two unmatchable bold-Unicode names and
   the accents, 8 of 11. Leaves `Ł` and every Devanagari name with a key nothing can match, and
   leaves docs/06 saying something the code does not do.
2. **Unidecode (GPL-2.0+).** What docs/06 names. Handles everything. Puts a copyleft licence in
   the worker image of a product that intends to sell, for a 300-line transliteration table.
3. **`anyascii` (ISC), 0.3.3, released 2025-06-29.** Same job — Unicode to ASCII transliteration,
   pure Python, no native build. Permissive licence, so no question to answer later. Covers the
   compatibility forms, `Ł`, and Devanagari.
4. **Strip anything non-ASCII from the key.** Free and wrong: it turns a Devanagari-only name into
   an empty key, and `normalized_name` is NOT NULL and is the blocking key.

## Decision

**`anyascii` (option 3), and strip legal suffixes as docs/06 asks.**

`anyascii` over `unidecode` purely for the licence: the two do the same work and only one of them
is a conversation with a lawyer. docs/06 section 6 is amended to name it, so the doc and the code
agree.

The legal suffixes are stripped although they merge nothing today, because the eight names that
carry one are B2B listings and a customer importing their own CRM export will carry far more —
"Acme Industries Pvt Ltd" and "Acme Industries" are one company, and the cheapest moment to agree
on that is before the second list arrives.

Transliteration is right for this key in particular because `normalized_name` is a *blocking* key,
not an identity. It is deliberately not unique — `primary_domain` is the dedupe handle (ADR-0007)
— so folding two spellings together proposes a candidate pair for scoring rather than merging
anything. A wider block costs comparisons; a narrower one loses the match entirely.

## Consequences

- One new worker dependency, `anyascii>=0.3.3`, pure Python and ISC.
- `normalized_name` changes for existing rows, so the 1,550 keys are backfilled in the same change
  — a key that disagrees with the function that produced it is worse than either spelling.
- Resolution (Phase 4, tasks 4.2–4.4) blocks a little wider. That is the intent; the labelled pair
  set in 4.4 is where it gets measured rather than asserted.
- `anyascii` transliterates: `सत्यम` becomes `satyam`, which is a claim about pronunciation, not a
  translation. It belongs in a matching key and must never be shown to a user as a business's
  name. `canonical_name` keeps what the source said, which is what the UI reads.
