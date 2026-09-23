-- Task identity, so a replanned job does not plan twice (task 2.10, docs/06 section "Idempotency").
--
-- docs/06 defines a task's identity as `{research_job_id}:{task_type}:{hash(input)}`. Until now
-- nothing held it. The planner writes a whole plan in one transaction, but the envelope that
-- triggers it can be redelivered: a worker that dies after committing, before acknowledging the
-- message, gets the same `research.plan` envelope again. Without a key the second run inserts a
-- second copy of every task, and the job then spends its whole budget twice over on identical
-- searches.
--
-- The Redis idempotency guard does not cover this. It stops a *concurrent* second delivery, and
-- its completion marker expires after seven days; neither makes an insert safe to repeat.
--
-- A generated column rather than one the worker supplies, because identity that the writer can
-- get wrong is not identity. `jsonb::text` is canonical for equal jsonb values (keys sorted,
-- insignificant whitespace dropped), so two runs of the same plan hash the same.

ALTER TABLE app.research_tasks
  ADD COLUMN task_key text GENERATED ALWAYS AS (type || ':' || md5(input::text)) STORED;

COMMENT ON COLUMN app.research_tasks.task_key IS
  'Identity of a task within its job (docs/06): type plus a hash of its input. Generated, never '
  'written. Unique per research_job_id, so replanning the same job is a no-op rather than a '
  'second bill for the same searches.';

-- Unique per job, not globally: two jobs asking the same question is normal and each pays for
-- its own answer. Partial on nothing, because every task has a type and an input.
CREATE UNIQUE INDEX research_tasks_job_key_uniq
  ON app.research_tasks (research_job_id, task_key);

-- The planner inserts with ON CONFLICT ... DO UPDATE against the index above, assigning
-- `attempts` to itself purely so the conflicting row's id is returned (DO NOTHING returns no
-- row, and the fan-out needs the id). That needs no extra grant: app_worker already has
-- UPDATE (attempts, ...) from migration 0013, and the generated column is computed by Postgres,
-- so a writer cannot supply or forge it.
--
-- Note it is a real UPDATE: a new tuple version is written and the research_tasks_updated_at
-- trigger sets updated_at = now(). A redelivered plan therefore refreshes updated_at on tasks
-- it does not otherwise touch.
