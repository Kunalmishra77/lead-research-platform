/**
 * Value retention (task 2.8a, ADR-0011): `app.sweep_expired_field_values`.
 *
 * Some sources licence their content for a limited time — Google Places content may be kept for
 * 30 days and must then be deleted. The thing worth testing hardest is the boundary: the sweeper
 * has to delete exactly what we are obliged to delete and nothing else, because everything else
 * is either ours or the customer's.
 *
 * Skipped without DATABASE_URL_MIGRATIONS.
 */
import postgres from 'postgres';
import { uuidv7 } from 'uuidv7';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const ownerUrl = process.env.DATABASE_URL_MIGRATIONS;
const live = Boolean(ownerUrl);

const DAY_MS = 24 * 60 * 60 * 1000;
const ago = (days: number): Date => new Date(Date.now() - days * DAY_MS);

describe.skipIf(!live)('field value retention', () => {
  const sql = postgres(ownerUrl ?? '', { max: 1, prepare: false, onnotice: () => undefined });
  const org = uuidv7();
  const workspace = uuidv7();
  const company = uuidv7();
  /** A source with a deletion obligation, and one without: the whole point of the column. */
  const perishable = { id: uuidv7(), key: `test_perishable_${org.slice(-8)}` };
  const durable = { id: uuidv7(), key: `test_durable_${org.slice(-8)}` };

  const addValue = async (sourceId: string, field: string, observedAt: Date): Promise<string> => {
    const id = uuidv7();
    await sql`
      insert into app.field_values
        (id, entity_type, entity_id, field, value, source_id, source_url, method, confidence,
         observed_at)
      values (${id}, 'company', ${company}, ${field}, ${JSON.stringify('x')}::jsonb,
              ${sourceId}, 'https://example.test/x', 'api', 0.9, ${observedAt})`;
    return id;
  };

  const surviving = async (): Promise<string[]> => {
    const rows = await sql<{ field: string }[]>`
      select field from app.field_values
      where entity_id = ${company} order by field`;
    return rows.map((r) => r.field);
  };

  const sweep = async (limit = 5000): Promise<number> => {
    const [row] = await sql<{ sweep_expired_field_values: number }[]>`
      select app.sweep_expired_field_values(${limit})`;
    return row?.sweep_expired_field_values ?? -1;
  };

  beforeAll(async () => {
    await sql`insert into app.organizations (id, name, slug) values (${org}, 'Retention Co', ${`ret-${org.slice(-12)}`})`;
    await sql`insert into app.workspaces (id, org_id, name) values (${workspace}, ${org}, 'Default')`;
    await sql`insert into app.companies (id, canonical_name, normalized_name)
                values (${company}, 'Example Co', 'example co')`;
    await sql`
      insert into app.sources (id, key, name, type, tos_class, default_ttl_days, retention_days)
      values (${perishable.id}, ${perishable.key}, 'Perishable', 'api', 'green', 30, 30),
             (${durable.id}, ${durable.key}, 'Durable', 'crawl', 'amber', 30, null)`;
  });

  afterAll(async () => {
    await sql`delete from app.field_values where entity_id = ${company}`;
    await sql`delete from app.companies where id = ${company}`;
    await sql`delete from app.organizations where id = ${org}`;
    await sql`delete from app.sources where id = any(${[perishable.id, durable.id]}::uuid[])`;
    await sql.end();
  });

  it('deletes only what the licence says must go', async () => {
    await addValue(perishable.id, 'expired_licensed', ago(31));
    await addValue(perishable.id, 'fresh_licensed', ago(29));
    await addValue(durable.id, 'old_but_ours', ago(400));

    const deleted = await sweep();

    expect(deleted).toBe(1);
    // A source with no retention_days is ours (or the customer's) to keep, however old.
    expect(await surviving()).toEqual(['fresh_licensed', 'old_but_ours']);
  });

  it('is exact at the boundary rather than approximate', async () => {
    await sql`delete from app.field_values where entity_id = ${company}`;
    // One hour either side of the 30-day line.
    await addValue(perishable.id, 'just_over', new Date(Date.now() - 30 * DAY_MS - 3600_000));
    await addValue(perishable.id, 'just_under', new Date(Date.now() - 30 * DAY_MS + 3600_000));

    await sweep();

    expect(await surviving()).toEqual(['just_under']);
  });

  it('works in batches, so one run never holds a partition for long', async () => {
    await sql`delete from app.field_values where entity_id = ${company}`;
    for (let i = 0; i < 5; i += 1) await addValue(perishable.id, `old_${i}`, ago(60));

    const first = await sweep(2);
    const second = await sweep(2);
    const third = await sweep(2);

    expect([first, second, third]).toEqual([2, 2, 1]);
    // Nothing left, and the caller knows to stop because the count reached zero.
    expect(await sweep(2)).toBe(0);
    expect(await surviving()).toEqual([]);
  });

  it('refuses a batch size that would mean an unbounded delete', async () => {
    await expect(sweep(0)).rejects.toThrow(/p_limit must be positive/);
  });

  it('is scheduled to run without anyone remembering to', async () => {
    const rows = await sql<{ schedule: string; active: boolean }[]>`
      select schedule, active from cron.job where jobname = 'app-sweep-expired-field-values'`;

    // A 30-day obligation kept by a job nobody runs is not kept at all.
    expect(rows).toHaveLength(1);
    expect(rows[0]?.active).toBe(true);
  });

  it('is not something an application role can call', async () => {
    const [row] = await sql<{ can: boolean }[]>`
      select has_function_privilege('app_api', 'app.sweep_expired_field_values(integer)', 'execute') as can`;

    // Wholesale deletion is maintenance, not something a request can trigger (ADR-0003).
    expect(row?.can).toBe(false);
  });

  it('keeps the seeded Places source on the clock its terms require', async () => {
    const [row] = await sql<{ retention_days: number | null; default_ttl_days: number }[]>`
      select retention_days, default_ttl_days from app.sources where key = 'google_places'`;

    // ADR-0011: 30 days, and retention is separate from freshness on purpose.
    expect(row?.retention_days).toBe(30);

    const [imported] = await sql<{ retention_days: number | null }[]>`
      select retention_days from app.sources where key = 'user_import'`;
    // The customer's own import has a 365-day freshness TTL and no deletion obligation at all.
    expect(imported?.retention_days).toBeNull();
  });
});
