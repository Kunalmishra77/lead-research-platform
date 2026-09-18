# Phase 6 — AI Research & Scoring

## Goal
Planner-executor-critic orchestration that fills gaps autonomously within budgets, AI entity adjudication, custom AI research columns, and configurable lead scoring.

## Read before starting
docs/06 sections 2, 9, 11; 07 (full); 09 (scoring UI, custom columns); 04 (scoring_models, custom_columns)

## Tasks
- [ ] 6.1 Critic loop (rules first, `critic_next_step` model second, validated against capability map); stop conditions
- [ ] 6.2 LLM match adjudicator for grey-zone pairs; results feed match_candidates; eval set
- [ ] 6.3 Scoring engine (gates, criteria, confidence weighting), website_quality derivation, score_breakdown
- [ ] 6.4 ICP fit AI score (optional weight) with reasons
- [ ] 6.5 Scoring model editor UI + default model from onboarding answers; rescoring job on model change
- [ ] 6.6 Custom research columns: define question + output type, extraction with evidence, per-lead cost
- [ ] 6.7 Embeddings for companies; lookalikes endpoint and "Find similar" action
- [ ] 6.8 Eval dashboard file `app/ai/evals/RESULTS.md` updated for all AI tasks
- [ ] 6.9 Cost guardrails: per-job LLM cap, cheaper-model routing verified by evals

## Acceptance criteria
- Critic increases required-field completion by >= 15 points vs Phase 5 on the same 500-lead test, within the same credit budget +20%.
- Adjudicator precision >= 0.95 on grey-zone eval set.
- Score breakdown explains 100% of score points; changing a model re-scores a 10k-lead workspace in < 2 minutes.
- Custom column answers include evidence links; answers without evidence are shown as "not found".
