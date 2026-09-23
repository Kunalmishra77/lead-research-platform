/**
 * CreditsService against the dev/test database (Phase 2 task 2.5): rates from app.credit_rates,
 * estimate, reserve with 402, settle. Skipped without DATABASE_URL + DATABASE_URL_MIGRATIONS.
 */
import { createDb } from '@leadforge/db';
import postgres from 'postgres';
import { uuidv7 } from 'uuidv7';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { AppError } from '../src/common/errors/app-error';
import { CreditsService } from '../src/modules/credits/credits.service';

const ownerUrl = process.env.DATABASE_URL_MIGRATIONS;
const apiUrl = process.env.DATABASE_URL;
const live = Boolean(ownerUrl && apiUrl);

describe.skipIf(!live)('CreditsService against the database', () => {
  const owner = postgres(ownerUrl ?? '', { max: 1, prepare: false, onnotice: () => undefined });
  const apiClient = postgres(apiUrl ?? '', { max: 1, prepare: false });
  const credits = new CreditsService(createDb(apiClient));
  const user = uuidv7();
  const org = uuidv7();
  const workspace = uuidv7();

  const newJob = async (): Promise<string> => {
    const id = uuidv7();
    await owner`insert into app.research_jobs (id, org_id, workspace_id, depth)
                values (${id}, ${org}, ${workspace}, 'standard')`;
    return id;
  };

  beforeAll(async () => {
    await owner`insert into auth.users (id, email, aud, role, email_confirmed_at)
                values (${user}, ${`credits-api-${user}@test.invalid`}, 'authenticated', 'authenticated', now())`;
    await owner`insert into app.organizations (id, name, slug, credits_balance)
                values (${org}, 'Credits API', ${`credits-api-${org.slice(-12)}`}, 100)`;
    await owner`insert into app.workspaces (id, org_id, name) values (${workspace}, ${org}, 'Default')`;
    await owner`insert into app.memberships (id, org_id, workspace_id, user_id, role)
                values (${uuidv7()}, ${org}, ${workspace}, ${user}, 'owner')`;
  });

  afterAll(async () => {
    try {
      await owner`delete from app.organizations where id = ${org}`;
      await owner`delete from auth.users where id = ${user}`;
    } finally {
      await Promise.all([owner.end(), apiClient.end()]);
    }
  });

  it('reads rates from the database, not from code', async () => {
    const rates = await credits.getRates(org, user);
    const [row] = await owner<{ credits_per_unit: number }[]>`
      select credits_per_unit from app.credit_rates where meter = 'research_standard'`;
    expect(rates.get('research_standard')).toBe(row?.credits_per_unit);
    expect(rates.get('search_stored')).toBe(0);
  });

  it('estimates a run at the configured rate and respects the spec budget', async () => {
    const standard = await credits.estimate(org, user, {
      depth: 'standard',
      maxResults: 200,
      maxCredits: 2000,
    });
    expect(standard).toMatchObject({
      meter: 'research_standard',
      reserve: standard.creditsPerLead * 200,
      cappedByBudget: false,
    });
    const capped = await credits.estimate(org, user, {
      depth: 'deep',
      maxResults: 500,
      maxCredits: 100,
    });
    expect(capped).toMatchObject({ reserve: 100, cappedByBudget: true });
  });

  it('reserves, settles and reports the balance', async () => {
    const job = await newJob();
    expect(await credits.balance(org, user)).toBe(100);
    await credits.reserve(org, user, job, 30);
    expect(await credits.balance(org, user)).toBe(70);

    const eventId = uuidv7();
    await owner`insert into app.usage_events (id, org_id, research_job_id, meter, units, credits, unit_key)
                values (${eventId}, ${org}, ${job}, 'research_standard', 1, 9, 'lead-1')`;
    await owner`insert into app.usage_unit_keys (org_id, research_job_id, meter, unit_key, usage_event_id)
                values (${org}, ${job}, 'research_standard', 'lead-1', ${eventId})`;

    expect(await credits.settle(org, user, job)).toEqual({ consumed: 9, released: 21 });
    expect(await credits.balance(org, user)).toBe(91);
    // Settling again changes nothing.
    expect(await credits.settle(org, user, job)).toEqual({ consumed: 0, released: 0 });
    expect(await credits.balance(org, user)).toBe(91);
  });

  it('refuses a run the balance cannot cover with 402', async () => {
    const job = await newJob();
    const before = await credits.balance(org, user);
    await expect(credits.reserve(org, user, job, before + 1)).rejects.toMatchObject({
      code: 'credits.insufficient',
      httpStatus: 402,
      errorClass: 'budget_exhausted',
    });
    await expect(credits.reserve(org, user, job, before + 1)).rejects.toBeInstanceOf(AppError);
    expect(await credits.balance(org, user)).toBe(before);
  });
});
