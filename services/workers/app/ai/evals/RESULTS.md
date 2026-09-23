# Eval results

One row per run (docs/07). Add a row before making a prompt or model change the default, so a
change is always a number that moved rather than an opinion.

```
uv run python -m app.ai.evals run <task>                  # live, costs money
uv run python -m app.ai.evals run <task> --record         # live, and save the answers
uv run python -m app.ai.evals run <task> --replay         # score the recorded answers, free
```

`pass_rate` is the share of cases where every checked field matched. `field_accuracy` is the
share of individual fields that matched, which tells you whether a failure was a near miss or a
different answer entirely. `cost/1k` is what 1000 cases of this task cost us in micros
(1 micro = 1e-6 USD), computed from the run's own token counts.

The adopted prompt version is pinned in `app/ai/config.py`, not inferred from the highest file
number, so a new prompt file changes nothing until someone adopts it here.

## intent_classify

| Date | Model | Prompt | Cases | Pass rate | Field acc. | Cost/1k | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-09-23 | gpt-5.4-mini-2026-03-17 | v1 | 50 | 96.0% | 96.0% | — | Two misses: a furniture manufacturer and an event management company read as `company_list` because they are worded as companies. |
| 2026-09-23 | gpt-5.4-mini-2026-03-17 | v2 | 50 | 92.0% | 92.0% | — | **Regression, not adopted.** Fixed both v1 misses but pushed "findable on a map" too far: four requests naming no place at all became `local_business`. |
| 2026-09-23 | gpt-5.4-mini-2026-03-17 | v3 | 50 | 98.0% | 98.0% | — | v1's framing plus the map test, with "a place must be named". One miss: a state-wide search, because the wording treated any region as unsearchable. |
| 2026-09-23 | gpt-5.4-mini-2026-03-17 | v4 | 50 | 100.0% | 100.0% | — | A state or district counts as a named place; a loose region does not. |
| 2026-09-23 | gpt-5.4-mini-2026-03-17 | **v5** | 56 | **100.0%** | 100.0% | 284,375 | Current default. v4 with every in-prompt example rewritten so no eval query appears in it (see "How honest is 100%"), and six new cases: two prompt-injection, four non-India. |

## spec_parse

| Date | Model | Prompt | Reasoning | Cases | Pass rate | Field acc. | Cost/1k | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-09-23 | gpt-5.4-mini-2026-03-17 | v1 | low | 50 | 88.0% | 96.2% | — | Below the phase's 90% bar. Three causes: "all the X in Y" read as `market_map`; qualifiers dropped from industry terms; and three cases where the scorer demanded an exact phrase rather than the same meaning. |
| 2026-09-23 | gpt-5.4-mini-2026-03-17 | v2 | low | 50 | 100.0% | 100.0% | — | `market_map` must be about the market itself; qualifiers that change which businesses match are kept. |
| 2026-09-23 | gpt-5.4-mini-2026-03-17 | v3 | low | 58 | 96.6% | 99.0% | 823,190 | v2 with de-leaked examples, plus the new cases and the newly-scored fields (seed company, radius, employee range, negative keywords, unsupported constraints, the confirmation gate). |
| 2026-09-23 | gpt-5.4-mini-2026-03-17 | **v4** | low | 59 | 94.9% | 98.0% | 843,864 | Adds "put anything the user ruled out in `keywords.not`". |
| 2026-09-23 | gpt-5.4-mini-2026-03-17 | **v4** | **medium** | 59 | **91.5% – 100%** | 97.0% – 100% | ~1,134,000 | Current default. Six runs: 100, 98.3, 98.3, 96.6, 96.6, 91.5 (mean ≈ 96.9%). Raising reasoning effort from low costs ~37% more and removes most of the run-to-run swing; the recorded baseline is a 100% run. |

### The one case that is genuinely unstable

`sp-036` ("interior designers in Gurgaon, not the big chains") is the case that moves between
runs. The model variously puts "big chains" in `keywords.not`, in `unsupported`, or drops it.
Its expectation is now `unsupported`, because "big chains" is a judgement we cannot filter on and
surfacing it forces a confirmation rather than quietly charging for unfiltered results — but the
model only reaches that answer most of the time, not always. **Vague exclusions are a known
weakness of this prompt.** It is kept as a case rather than tuned away, so the next prompt change
has something to beat.

### Two cases that were wrong, not the model

- `sp-019` "more than 100 employees" expected `gte: 100`; the model returned `gte: 101`, which is
  what "more than" means.
- `sp-029` "travel agencies in Kochi" expected `country: IN`. Kochi is in Kerala and Kōchi is in
  Japan, so refusing to guess is correct; the country pin was removed and the planner resolves
  the city against the geo seed instead.

### What changed in the scorer on 2026-09-23

Free-text fields (`industry`, `keywords_must`, `keywords_not`, `unsupported`) are compared word
for word by default, because adding a qualifier narrows a search exactly as much as dropping one
widens it. A case that has more than one honest reading opts out with `"allow_extra_words": true`
— that is how "NEET" is satisfied by "NEET preparation" while "cbse school" is still not
satisfied by "school".

## query_expand

| Date | Model | Prompt | Cases | Pass rate | Field acc. | Cost/1k | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-09-23 | gpt-5.4-mini-2026-03-17 | v1 (draft) | 81 | 91.4% | 96.9% | 440,309 | First run. Two prompt contradictions and four wrong expectations, below. |
| 2026-09-23 | gpt-5.4-mini-2026-03-17 | v1 (draft) | 81 | 92.6% | 96.9% | 472,198 | Contradictions fixed. Six mid-sized Indian cities returned **no** areas at all. |
| 2026-09-23 | gpt-5.4-mini-2026-03-17 | v1 (draft) | 81 | 100.0% | 100.0% | 526,531 | The size rule made explicit — but it named the three cities that had failed, so this number is partly recall. |
| 2026-09-23 | gpt-5.4-mini-2026-03-17 | v1 (draft) | 81 | 97.5% | 98.5% | 528,728 | **De-leaked.** The population rule holds without the city names; the mid-sized-city failures did not return. Two new misses: "near Connaught Place" ranged 25 km across Delhi, and one check of mine was wrong. |
| 2026-09-23 | gpt-5.4-mini-2026-03-17 | **v1** | 81 | **100.0%** | 100.0% | 544,753 | Current default and the recorded baseline. "Near an area still means that area." |

### Two contradictions in the draft prompt, found by the eval and not by reading it

- It asked for "the plain reading of the request" as the first phrase **and** for filters a maps
  search cannot read to be left out. For "startups in Bangalore that raised Series A" those are
  opposite instructions, and the model obeyed the first. A funding stage in a maps query does not
  narrow the search, it corrupts it.
- It said to return an empty list when the request already names an area inside a city. But
  "Bandra" splits into "Bandra West" and "Bandra East", and that covers Bandra *better* than one
  search does. The rule that matters is staying **inside** the named area, not declining to
  subdivide it; the cases now pin that instead.

### The failure that was worth the whole exercise

At 92.6%, the six remaining failures were all one behaviour: **Indore (3.2M), Rajkot (1.6M),
Ranchi (1.4M), Mysuru, Noida and Udaipur each came back with no sub-localities.** The prompt
offered only "large metro" or "small town", and the model filed all six under town.

That is not a cosmetic miss. Without areas the planner issues one Places call for a whole
mid-sized city, the API returns a capped page, and every business past the cap is simply never
seen — the user pays for a search that quietly returned a fraction of the market. Naming a
population figure ("roughly half a million or more has these, and needs them") and saying plainly
which mistake is the expensive one took it to 100%.

### Four expectations that were wrong, not the model

Corrected rather than tuned around, because each was a fact about the world:

- **Dublin** came back as "Dublin 2", "Dublin 4" — postal districts are what people there
  actually use. **Jakarta** came back as "South Jakarta", "Central Jakarta"; **Delhi** as its
  eleven administrative districts. For tiling, a complete administrative cover is arguably better
  than a dozen hand-picked neighbourhoods. `sub_localities_any_of` means "at least one real area
  of this place", so every real naming family belongs in it.
- **Thrissur** and **Karnal** (~300k, a planned city with genuine numbered sectors) sit exactly on
  the line where one search may or may not be enough. Neither we nor the model can settle that, so
  those cases pin only what is certain. Pithoragarh, Sivakasi and Chikmagalur carry the "nothing
  inside to name" behaviour, and they are not borderline.

### What this set does not yet prove

The same caution as the sections above applies, and one more: **the scorer never checks that a
sub-locality is real.** It checks that at least one recognised name appears, that the count is
plausible, and that no other city leaks in. A confidently invented neighbourhood inside an
otherwise good list would pass, and would cost one wasted Places call per job. Checking that
needs the geo seed, which the planner reads anyway — worth doing once discovery is running and
real yield per area can be compared.

## How honest is 100%

Not as honest as it looks, and here is the arithmetic.

**Prompts were revised in response to failures in these same sets.** That makes the sets a
regression guard, not independent evidence about the behaviours they drove. `intent_classify` v2
is kept in the repo precisely because it is the counter-example: a change that read as obviously
correct and measured worse.

**In-prompt examples were leaking.** Before v5/v4, 14 of 50 `intent_classify` case queries and 10
of 50 `spec_parse` queries appeared verbatim in the prompt, next to their correct answer — those
cases were scored on memory. Every example has been rewritten to phrasings that appear in no case
file. The numbers above for v5 and v4 are measured on that basis; every earlier row is not.

That used to be a hand check, and this section claimed it as a guarantee. It is now a test
(`test_no_case_is_answered_for_the_model_inside_its_own_prompt`), which was worth writing: it
immediately found two fresh leaks in the `query_expand` draft, one of them naming the exact three
cities whose failures had prompted the rule it sat beneath. Removing those names cost 2.5 points
on the first re-run — the rule still held on its own, but part of that score had been recall.
Single-word queries ("leads", "business") are exempt, because finding a common word in prose
about lead research proves nothing.

**A single run is not the score.** `spec_parse` ranges over 8.5 points between runs. Quote the
range, not the best run. The recorded baseline is a 100% run because a replay needs a clean
fixture, and its `meta.pass_rate` records exactly what it scored.

The next real signal comes from cases nobody has tuned against (docs/07 says grow towards 300+):

- queries from real users once the parse endpoint is live (task 2.4);
- more Hinglish and transliterated input — three cases here;
- place names that collide across states and countries, which is where a wrong guess costs
  credits;
- constraints we cannot express, to check they are surfaced rather than dropped;
- more of the minority intents (`market_map`, `competitor_scan`, `hiring_signal`,
  `single_company` have four cases each, where one case moves the number by 1.7 points).
