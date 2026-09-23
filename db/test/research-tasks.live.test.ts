/**
 * Task identity (task 2.10, migration 0018): `research_tasks.task_key`.
 *
 * The planner writes a whole plan in one transaction, but the `research.plan` envelope that
 * triggers it can be redelivered — a worker that dies after committing and before acknowledging
 * gets the same envelope again. Without an identity the second run inserts a second copy of every
 * task and the job spends its entire budget twice on identical searches.
 *
 * So what is tested here is not that a column exists but that it cannot be got wrong: that it is
 * computed rather than supplied, that equal inputs written differently still collide, and that
 * two jobs asking the same question each keep their own answer.
 *
 * Skipped without DATABASE_URL_MIGRATIONS.
 */
import postgres from 'postgres';
import { uuidv7 } from 'uuidv7';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const ownerUrl = process.env.DATABASE_URL_MIGRATIONS;
const workerUrl = process.env.DATABASE_URL_WORKERS;
const live = Boolean(ownerUrl && workerUrl);

describe.skipIf(!live)('research task identity', () => {
  const sql = postgres(ownerUrl ?? '', { max: 1, prepare: false, onnotice: () => undefined });
  /** The role the planner actually runs as: RLS forced, and column-level UPDATE grants. */
  const worker = postgres(workerUrl ?? '', { max: 1, prepare: false, onnotice: () => undefined });
  const org = uuidv7();
  const workspace = uuidv7();
  const search = uuidv7();
  const jobA = uuidv7();
  const jobB = uuidv7();

  const TYPE = 'discovery.places_text_search';

  const insert = async (
    jobId: string,
    input: unknown,
    opts: { type?: string; budget?: number } = {},
  ): Promise<string | null> => {
    const rows = await sql<{ id: string }[]>`
      insert into app.research_tasks (id, org_id, research_job_id, type, input, credit_budget)
      values (${uuidv7()}, ${org}, ${jobId}, ${opts.type ?? TYPE},
              ${sql.json(input as never)}, ${opts.budget ?? 10})
      on conflict (research_job_id, task_key) do nothing
      returning id`;
    return rows[0]?.id ?? null;
  };

  const countFor = async (jobId: string): Promise<number> => {
    const [row] = await sql<{ n: number }[]>`
      select count(*)::int as n from app.research_tasks where research_job_id = ${jobId}`;
    return row?.n ?? -1;
  };

  beforeAll(async () => {
    await sql`insert into app.organizations (id, name, slug)
              values (${org}, 'Planner Co', ${`plan-${org.slice(-12)}`})`;
    await sql`insert into app.workspaces (id, org_id, name) values (${workspace}, ${org}, 'Default')`;
    await sql`insert into app.searches (id, org_id, workspace_id, raw_query, spec, spec_version)
              values (${search}, ${org}, ${workspace}, 'dental clinics in Pune', '{}'::jsonb, 1)`;
    for (const id of [jobA, jobB]) {
      await sql`insert into app.research_jobs (id, org_id, workspace_id, search_id, status)
                values (${id}, ${org}, ${workspace}, ${search}, 'planning')`;
    }
  }, 30_000);

  afterAll(async () => {
    await sql`delete from app.research_tasks where org_id = ${org}`;
    await sql`delete from app.research_jobs where org_id = ${org}`;
    await sql`delete from app.searches where org_id = ${org}`;
    await sql`delete from app.organizations where id = ${org}`;
    await worker.end();
    await sql.end();
  }, 30_000);

  it('plans a job once, however many times the envelope is delivered', async () => {
    const input = {
      source: 'google_places',
      query: { text: 'dental clinics', bbox: [1, 2, 3, 4] },
    };

    const first = await insert(jobA, input);
    const second = await insert(jobA, input);

    expect(first).not.toBeNull();
    // The second delivery buys nothing. Before this key it bought a second copy of the plan.
    expect(second).toBeNull();
    expect(await countFor(jobA)).toBe(1);
  });

  it('sees through key order, because jsonb stores a value and not its text', async () => {
    const input = { b: 2, a: 1, nested: { y: [1, 2], x: 'q' } };
    const reordered = { nested: { x: 'q', y: [1, 2] }, a: 1, b: 2 };

    expect(await insert(jobB, input)).not.toBeNull();
    // Two writers serialising the same plan differently must still collide, or idempotency
    // would depend on which client wrote it. This only holds while the value reaches Postgres
    // as jsonb: passing it as a pre-serialised string stores a jsonb *string* instead, which
    // keeps the original byte order and quietly defeats the whole key. Writing this test wrong
    // the first time is what proved it.
    expect(await insert(jobB, reordered)).toBeNull();
  });

  it('canonicalises what the worker actually sends, which is text cast to jsonb', async () => {
    // services/workers/app/db/research_tasks.py sends `json.dumps(...)` bound to
    // `cast(:input as jsonb)`, which asyncpg delivers as text that Postgres then parses. That
    // is the path that runs in production, so it gets its own test.
    //
    // The explicit `cast(... as text)` below is not decoration: postgres.js infers the bind
    // type from the cast and would otherwise send the string as a jsonb value, storing a jsonb
    // *string* whose bytes are kept verbatim. Two equal plans written differently would then
    // hash differently and every redelivery would buy the plan again.
    const asText = async (jobId: string, raw: string): Promise<string | null> => {
      const rows = await sql<{ id: string }[]>`
        insert into app.research_tasks (id, org_id, research_job_id, type, input, credit_budget)
        values (${uuidv7()}, ${org}, ${jobId}, ${TYPE}, cast(cast(${raw} as text) as jsonb), 10)
        on conflict (research_job_id, task_key) do nothing
        returning id`;
      return rows[0]?.id ?? null;
    };

    expect(await asText(jobB, '{"z": 1, "a": {"n": [1, 2]}}')).not.toBeNull();
    // Same value, different text: different key order and extra whitespace.
    expect(await asText(jobB, '{ "a" : { "n" : [1,2] },   "z":1 }')).toBeNull();
  });

  it('keeps two jobs asking the same question apart', async () => {
    const input = { source: 'google_places', query: { text: 'gyms' } };

    expect(await insert(jobA, input)).not.toBeNull();
    // Unique per job, not globally: two customers searching the same market each pay for, and
    // each receive, their own answer.
    expect(await insert(jobB, input)).not.toBeNull();
  });

  it('separates two searches that differ only in where they look', async () => {
    const pune = {
      source: 'google_places',
      query: { text: 'cafes', bbox: [18.4, 73.6, 18.6, 74] },
    };
    const mumbai = {
      source: 'google_places',
      query: { text: 'cafes', bbox: [18.9, 72.7, 19.3, 73] },
    };

    expect(await insert(jobA, pune)).not.toBeNull();
    expect(await insert(jobA, mumbai)).not.toBeNull();
  });

  it('separates two task types over the same input', async () => {
    const input = { source: 'google_places', query: { text: 'salons' } };

    expect(await insert(jobA, input)).not.toBeNull();
    expect(await insert(jobA, input, { type: 'discovery.places_nearby' })).not.toBeNull();
  });

  it("runs the planner's own statement, as the planner's own role", async () => {
    // Everything above connects as the owner, which bypasses RLS and column grants, and uses
    // DO NOTHING. Production is app_worker under forced RLS running ON CONFLICT ... DO UPDATE.
    // That difference is where the three things that could actually break live, so it gets a
    // test of its own: the grant must permit the self-assignment, the conflicting row's id must
    // come back (DO NOTHING returns none, and the fan-out has nothing to publish), and `inserted`
    // must tell a fresh plan from a redelivered one.
    const plan = async (input: unknown): Promise<{ id: string; inserted: boolean }> => {
      const rows = await worker.begin(async (tx) => {
        await tx`select set_config('app.org_id', ${org}, true), set_config('app.user_id', '', true)`;
        return tx<{ id: string; inserted: boolean }[]>`
          insert into app.research_tasks
              (id, org_id, research_job_id, parent_task_id, type, input, credit_budget)
          values (${uuidv7()}, ${org}, ${jobA}, ${null}, ${TYPE},
                  cast(cast(${JSON.stringify(input)} as text) as jsonb), 7)
          on conflict (research_job_id, task_key)
              do update set attempts = app.research_tasks.attempts
          returning id, (xmax = 0) as inserted`;
      });
      const row = rows[0];
      if (!row) throw new Error('the upsert returned no row, so the fan-out would have nothing');
      return row;
    };

    const input = { source: 'google_places', query: { text: 'opticians' } };
    const first = await plan(input);
    expect(first.inserted).toBe(true);

    const second = await plan(input);
    // The whole reason DO UPDATE was chosen over DO NOTHING.
    expect(second.id).toBe(first.id);
    expect(second.inserted).toBe(false);

    const [row] = await sql<{ credit_budget: number; attempts: number }[]>`
      select credit_budget, attempts from app.research_tasks where id = ${first.id}`;
    expect(row?.credit_budget).toBe(7);
    expect(row?.attempts).toBe(0);
  });

  it('cannot be written by hand, so a writer cannot claim a false identity', async () => {
    await expect(
      sql`insert into app.research_tasks (id, org_id, research_job_id, type, input, task_key)
          values (${uuidv7()}, ${org}, ${jobA}, ${TYPE}, '{}'::jsonb, 'anything-i-like')`,
    ).rejects.toThrow(/non-DEFAULT value into column "task_key"/i);
  });

  it('ignores the budget, so a replan cannot smuggle in a second allowance', async () => {
    const input = { source: 'google_places', query: { text: 'bakeries' } };

    const first = await insert(jobA, input, { budget: 10 });
    const second = await insert(jobA, input, { budget: 9_999 });

    expect(first).not.toBeNull();
    // Identity is the work, not what it was funded with. A redelivery asking for more must not
    // become a second row — and app_worker cannot update credit_budget either (migration 0013).
    expect(second).toBeNull();
    const [row] = await sql<{ credit_budget: number }[]>`
      select credit_budget from app.research_tasks where id = ${first}`;
    expect(row?.credit_budget).toBe(10);
  });
});
