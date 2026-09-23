import { describe, expect, it } from 'vitest';

import { estimateFor } from './credits.service';

describe('estimateFor', () => {
  it('prices a run at the configured rate per delivered lead', () => {
    expect(estimateFor(3, { depth: 'standard', maxResults: 200, maxCredits: 2000 })).toEqual({
      depth: 'standard',
      meter: 'research_standard',
      creditsPerLead: 3,
      maxResults: 200,
      reserve: 600,
      cappedByBudget: false,
    });
  });

  it("never reserves more than the spec's own credit limit", () => {
    const capped = estimateFor(8, { depth: 'deep', maxResults: 500, maxCredits: 100 });
    expect(capped).toMatchObject({ reserve: 100, cappedByBudget: true });
  });

  it('keeps a free meter free', () => {
    expect(estimateFor(0, { depth: 'quick', maxResults: 50, maxCredits: 10 })).toMatchObject({
      reserve: 0,
      cappedByBudget: false,
    });
  });
});
