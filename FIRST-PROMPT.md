# First prompt for Claude Code

## Setup (one time, ~5 minutes)

1. Unzip `lead-research-platform.zip` and move the folder where you keep projects.
2. Open the folder in VS Code.
3. In the terminal:
   ```bash
   git init
   git add .
   git commit -m "chore: add planning docs, phases and Claude Code config"
   ```
4. Make sure installed: Node LTS, pnpm, Python 3.12, uv, Docker Desktop, Git.
5. Start Claude Code in this folder (`claude` in the terminal, or the VS Code extension panel).
6. Optional: type `/help` and check that `/start-phase`, `/next-task`, `/finish-task`, `/review-phase`, `/new-connector`, `/adr` appear.
7. Before real API work, create `.env` from `.env.example` yourself. Claude is blocked from reading `.env` by `.claude/settings.json`.

Tip: use Plan Mode (Shift+Tab) for the first prompt so Claude only reads and plans.

---

## Prompt 1 — Orientation (paste this first)

```
You are the lead engineer on this repository. It is currently documentation only: CLAUDE.md, docs/, phases/, .claude/ commands and agents. Nothing has been built yet.

Do this, and do NOT write or change any code or files yet:

1. Read CLAUDE.md fully.
2. Read docs/00-PRODUCT-BRIEF.md, docs/01-ARCHITECTURE.md, docs/02-TECH-STACK.md, docs/03-FOLDER-STRUCTURE.md, docs/adr/0001-stack-and-job-contract.md and phases/PHASES.md.
3. Skim the headings of all other docs/ files and all phases/ files so you know where information lives.
4. Give me:
   a) A 10-bullet summary of the product and architecture in your own words.
   b) Any contradictions, gaps or risky assumptions you found across the docs (be specific: file + section).
   c) Tool/version checks I should run on my machine before Phase 1 (node, pnpm, python, uv, docker) with exact commands.
   d) The decisions that must be made before Phase 1 coding (auth integration approach, confirm NestJS vs Laravel for apps/api, SERP vendor can wait) with your recommendation for each.
5. Stop and wait for my answers.
```

## Prompt 2 — Start Phase 1 (after you answer the questions)

```
/start-phase 1
```

Approve the plan (or ask for changes). Then for each task:

```
/next-task
```
→ review Claude's short plan → reply `go` → when done:
```
Use the code-reviewer agent on the current diff.
```
→ fix blockers →
```
/finish-task
```

When all tasks are ticked:
```
/review-phase 1
```

## Starting any new session later

```
Read CLAUDE.md, the latest 3 entries in PROGRESS.md, and the current in-progress phase file. Tell me in 5 bullets where we are and what the next task is. Do not code yet.
```

## If Claude drifts

```
Stop. Re-read CLAUDE.md non-negotiable rules and the current phase file. List what you changed that is outside this task's scope, and revert or explain each item.
```
