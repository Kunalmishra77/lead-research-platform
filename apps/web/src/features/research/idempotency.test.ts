import type { ResearchSpec } from '@leadforge/contracts';
import { describe, expect, it } from 'vitest';

import { idempotencyKeyFor } from './idempotency';

const SPEC = {
  depth: 'quick',
  filters: {},
  limits: { max_results: 20 },
} as unknown as ResearchSpec;

/**
 * The API replays a repeated Idempotency-Key for 24 hours. That is the right behaviour for a
 * double-clicked button and the wrong behaviour for "run this search again tomorrow morning",
 * so what the key is built from decides which of the two the product supports.
 */
describe('idempotencyKeyFor', () => {
  it('is the same for two clicks of one reviewed request', () => {
    // The reason the key exists: a second click must not start a second job or reserve credits
    // for one.
    expect(idempotencyKeyFor('visit-1', 'dental clinics in Delhi', SPEC)).toBe(
      idempotencyKeyFor('visit-1', 'dental clinics in Delhi', SPEC),
    );
  });

  it('differs between visits, so the same search can be run again', () => {
    // The bug this is here for: the key was a hash of the content alone, so asking for the same
    // leads twice in a day returned the first run's job and did no work.
    expect(idempotencyKeyFor('visit-1', 'dental clinics in Delhi', SPEC)).not.toBe(
      idempotencyKeyFor('visit-2', 'dental clinics in Delhi', SPEC),
    );
  });

  it('differs when the request itself changes within one visit', () => {
    expect(idempotencyKeyFor('visit-1', 'dental clinics in Delhi', SPEC)).not.toBe(
      idempotencyKeyFor('visit-1', 'dental clinics in Mumbai', SPEC),
    );
    expect(idempotencyKeyFor('visit-1', 'dental clinics in Delhi', SPEC)).not.toBe(
      idempotencyKeyFor('visit-1', 'dental clinics in Delhi', {
        ...SPEC,
        depth: 'deep',
      }),
    );
  });
});
