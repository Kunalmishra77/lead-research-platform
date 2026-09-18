---
description: Check a phase against its acceptance criteria before closing it
argument-hint: <phase-number>
---
Review Phase $ARGUMENTS.

1. Open the phase file for this number in `phases/` (zero-padded, e.g. `phase-01-foundation.md`).
2. For every task: confirm it is implemented (point to files) or list what is missing.
3. For every acceptance criterion: run or describe the exact check and report PASS/FAIL with evidence.
4. Check non-negotiable rules from `CLAUDE.md` (provenance, RLS, secrets, compliance stops, usage metering) for code added in this phase.
5. List tech debt created and propose follow-ups.
6. Only if everything passes: mark the phase `done` in `phases/PHASES.md` with today's date and add a PROGRESS.md entry.
