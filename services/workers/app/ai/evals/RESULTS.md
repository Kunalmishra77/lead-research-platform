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

## How honest is 100%

Not as honest as it looks, and here is the arithmetic.

**Prompts were revised in response to failures in these same sets.** That makes the sets a
regression guard, not independent evidence about the behaviours they drove. `intent_classify` v2
is kept in the repo precisely because it is the counter-example: a change that read as obviously
correct and measured worse.

**In-prompt examples were leaking.** Before v5/v4, 14 of 50 `intent_classify` case queries and 10
of 50 `spec_parse` queries appeared verbatim in the prompt, next to their correct answer — those
cases were scored on memory. Every example has been rewritten to phrasings that appear in no case
file, and a check confirms zero verbatim overlap. The numbers above for v5 and v4 are measured on
that basis; every earlier row is not.

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
