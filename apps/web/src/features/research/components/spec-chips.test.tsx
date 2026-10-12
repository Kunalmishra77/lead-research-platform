import type { ResearchSpec } from '@leadforge/contracts';
import { describe, expect, it } from 'vitest';

import { chipGroups } from './spec-chips';

/**
 * These chips are how someone checks what we understood before spending credits on it, so what
 * matters is that a filter they asked for appears — including the negative one.
 *
 * `has_website: false` is a market of its own: who to sell a website to. It showed no chip at
 * all, which reads as a filter that was dropped, and for a while it genuinely was — the spec
 * carried it and nothing in the pipeline read it.
 */
function spec(filters: Partial<ResearchSpec['filters']>): ResearchSpec {
  return {
    spec_version: 1,
    entity: 'company',
    intent: 'prospecting',
    filters: { location: { country: 'IN', cities: ['Delhi'] }, ...filters },
    fields: ['name'],
    depth: 'standard',
    limits: { max_results: 250, max_credits: 500 },
  } as unknown as ResearchSpec;
}

function otherChips(filters: Partial<ResearchSpec['filters']>): string[] {
  return chipGroups(spec(filters)).find((g) => g.key === 'other')?.chips ?? [];
}

describe('spec chips', () => {
  it('says so when a website is required', () => {
    expect(otherChips({ has_website: true })).toContain('has a website');
  });

  it('says so when a website is ruled out', () => {
    expect(otherChips({ has_website: false })).toContain('has no website');
  });

  it('says nothing about websites when the spec does not', () => {
    const chips = otherChips({});
    expect(chips.join(' ')).not.toContain('website');
  });
});
