/**
 * Export and prune (ADR-0016): `app.sweep_exported_field_values`.
 *
 * This function deletes, and what it deletes belongs to customers. The property worth testing
 * hardest is not that it frees space -- that is arithmetic -- but that it never takes a value
 * somebody still needs. `field_values` hang off a *company*, and the company graph is shared
 * between every tenant, so one customer exporting a business must not blank it out for another
 * who is still working it.
 *
 * Skipped without DATABASE_URL_MIGRATIONS.
 */
import postgres from 'postgres';
import { uuidv7 } from 'uuidv7';
import { afterAll, beforeEach, describe, expect, it } from 'vitest';

const ownerUrl = process.env.DATABASE_URL_MIGRATIONS;
const live = Boolean(ownerUrl);

describe.skipIf(!live)('pruning exported leads', () => {
  const sql = postgres(ownerUrl ?? '', { max: 1, prepare: false, onnotice: () => undefined });
  const suffix = uuidv7().slice(-12);
  const orgA = uuidv7();
  const orgB = uuidv7();
  const workspaceA = uuidv7();
  const workspaceB = uuidv7();
  const source = { id: uuidv7(), key: `test_prune_${suffix}` };

  /** A company nobody else in the database shares, so a sweep here cannot disturb real rows. */
  const makeCompany = async (name: string): Promise<string> => {
    const id = uuidv7();
    await sql`insert into app.companies (id, canonical_name, normalized_name)
                values (${id}, ${name}, ${name.toLowerCase()})`;
    await sql`
      insert into app.field_values
        (id, entity_type, entity_id, field, value, source_id, source_url, method, confidence,
         observed_at)
      values (${uuidv7()}, 'company', ${id}, 'email', ${JSON.stringify('a@b.test')}::jsonb,
              ${source.id}, 'https://example.test/x', 'provider', 0.85, now()),
             (${uuidv7()}, 'company', ${id}, 'phone', ${JSON.stringify('+911141000000')}::jsonb,
              ${source.id}, 'https://example.test/x', 'provider', 0.85, now())`;
    return id;
  };

  const deliver = async (
    workspace: string,
    org: string,
    company: string,
    exportedDaysAgo: number | null,
  ): Promise<void> => {
    const exportedAt =
      exportedDaysAgo === null ? null : new Date(Date.now() - exportedDaysAgo * 86_400_000);
    await sql`insert into app.leads (id, org_id, workspace_id, company_id, exported_at)
                values (${uuidv7()}, ${org}, ${workspace}, ${company}, ${exportedAt})`;
  };

  const valuesOf = async (company: string): Promise<number> => {
    const [row] = await sql<{ n: number }[]>`
      select count(*)::int as n from app.field_values where entity_id = ${company}`;
    return row?.n ?? -1;
  };

  const sweep = async (graceDays = 30): Promise<number> => {
    const [row] = await sql<{ sweep_exported_field_values: number }[]>`
      select app.sweep_exported_field_values(${graceDays}, 5000)`;
    return row?.sweep_exported_field_values ?? -1;
  };

  const companies: string[] = [];
  const track = async (name: string): Promise<string> => {
    const id = await makeCompany(name);
    companies.push(id);
    return id;
  };

  beforeEach(async () => {
    await sql`insert into app.organizations (id, name, slug) values
                (${orgA}, 'Prune A', ${`prune-a-${suffix}`}),
                (${orgB}, 'Prune B', ${`prune-b-${suffix}`})
              on conflict do nothing`;
    await sql`insert into app.workspaces (id, org_id, name) values
                (${workspaceA}, ${orgA}, 'Default'), (${workspaceB}, ${orgB}, 'Default')
              on conflict do nothing`;
    await sql`insert into app.sources (id, key, name, type, tos_class, default_ttl_days)
                values (${source.id}, ${source.key}, 'Prune test', 'provider', 'green', 30)
              on conflict do nothing`;
  });

  afterAll(async () => {
    await sql`delete from app.leads where workspace_id = any(${[workspaceA, workspaceB]}::uuid[])`;
    await sql`delete from app.field_values where entity_id = any(${companies}::uuid[])`;
    await sql`delete from app.companies where id = any(${companies}::uuid[])`;
    await sql`delete from app.organizations where id = any(${[orgA, orgB]}::uuid[])`;
    await sql`delete from app.sources where id = ${source.id}`;
    await sql.end();
  });

  it('leaves a lead nobody has exported completely alone', async () => {
    // The ceiling applies to work in progress, never to delivered work a customer has not
    // collected. A sweep that took these would be deleting leads somebody is about to use.
    const company = await track(`Unexported ${uuidv7().slice(-6)}`);
    await deliver(workspaceA, orgA, company, null);

    await sweep();

    expect(await valuesOf(company)).toBe(2);
  });

  it('leaves an export inside the grace window alone', async () => {
    const company = await track(`Recent ${uuidv7().slice(-6)}`);
    await deliver(workspaceA, orgA, company, 1);

    await sweep(30);

    expect(await valuesOf(company)).toBe(2);
  });

  it('prunes a lead exported longer ago than the grace window', async () => {
    const company = await track(`Cold ${uuidv7().slice(-6)}`);
    await deliver(workspaceA, orgA, company, 60);

    await sweep(30);

    expect(await valuesOf(company)).toBe(0);
  });

  it('keeps the lead, the company and its address when the values go', async () => {
    // A pruned lead is not a deleted one. Its name, domain, city, address and phone live in
    // `companies` and `company_locations`, so the customer still sees the business they were
    // given -- only the detail they already have in their CSV is gone.
    const company = await track(`Still Here ${uuidv7().slice(-6)}`);
    await sql`insert into app.company_locations (id, company_id, address, phone_e164)
                values (${uuidv7()}, ${company}, '12 MG Road', '+911141000001')`;
    await deliver(workspaceA, orgA, company, 60);

    await sweep(30);

    const [row] = await sql<{ name: string; address: string; phone: string; leads: number }[]>`
      select c.canonical_name as name, l.address, l.phone_e164 as phone,
             (select count(*)::int from app.leads where company_id = c.id) as leads
        from app.companies c
        join app.company_locations l on l.company_id = c.id
       where c.id = ${company}`;
    expect(row?.name).toContain('Still Here');
    expect(row?.address).toBe('12 MG Road');
    expect(row?.phone).toBe('+911141000001');
    expect(row?.leads).toBe(1);
  });

  it('never prunes a company another workspace is still working', async () => {
    // The property this function most has to get right. The graph is shared: two customers can
    // hold the same business, and one of them exporting it says nothing about the other.
    const company = await track(`Shared ${uuidv7().slice(-6)}`);
    await deliver(workspaceA, orgA, company, 60); // A exported it long ago
    await deliver(workspaceB, orgB, company, null); // B has not exported at all

    await sweep(30);

    expect(await valuesOf(company)).toBe(2);
  });

  it('prunes only once every holder has finished with it', async () => {
    const company = await track(`Both Done ${uuidv7().slice(-6)}`);
    await deliver(workspaceA, orgA, company, 60);
    await deliver(workspaceB, orgB, company, 45);

    await sweep(30);

    expect(await valuesOf(company)).toBe(0);
  });

  it('does not touch a company no lead points at', async () => {
    // Those belong to the source retention sweeper (0017), which deletes for a different
    // reason on a different clock. Two sweepers deleting the same rows for different reasons
    // is how a deletion bug becomes impossible to attribute.
    const company = await track(`Orphan ${uuidv7().slice(-6)}`);

    await sweep(30);

    expect(await valuesOf(company)).toBe(2);
  });

  it('reports how many rows it took, so a caller can sweep until it is done', async () => {
    const company = await track(`Counted ${uuidv7().slice(-6)}`);
    await deliver(workspaceA, orgA, company, 60);

    const first = await sweep(30);
    const second = await sweep(30);

    expect(first).toBeGreaterThanOrEqual(2);
    expect(second).toBe(0);
  });
});
