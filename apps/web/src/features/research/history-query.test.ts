import { describe, expect, it } from 'vitest';

import { allowedFilters, historyHref } from './history-query';

/**
 * The history's filters live in its URL, which means anyone can type one. What reaches the API
 * has to be something the API declared, or a hand-edited address becomes a 500 on a page the
 * person was only trying to read.
 */
describe('allowedFilters', () => {
  it('keeps a status the API knows', () => {
    expect(allowedFilters({ status: 'failed' })).toEqual({ status: 'failed' });
  });

  it('drops one it does not, rather than passing it on', () => {
    expect(allowedFilters({ status: 'exploded' })).toEqual({});
    expect(allowedFilters({ status: 'DROP TABLE leads' })).toEqual({});
  });

  it('trims a search term and ignores an empty one', () => {
    expect(allowedFilters({ q: '  gyms  ' })).toEqual({ q: 'gyms' });
    expect(allowedFilters({ q: '   ' })).toEqual({});
  });

  it('shortens a term past what the API accepts instead of being refused', () => {
    expect(allowedFilters({ q: 'x'.repeat(500) }).q).toHaveLength(200);
  });
});

describe('historyHref', () => {
  it('is the bare page when nothing is filtered', () => {
    expect(historyHref()).toBe('/research/history');
    expect(historyHref({ q: '  ' })).toBe('/research/history');
  });

  it('carries the filters that survived', () => {
    expect(historyHref({ status: 'completed', q: 'gyms in delhi' })).toBe(
      '/research/history?status=completed&q=gyms+in+delhi',
    );
  });

  it('leaves the cursor out unless it was asked for', () => {
    // Changing a filter has to start from the first page: page three of "all runs" is not page
    // three of "failed ones", and an out-of-range cursor reads as "no results".
    expect(historyHref({ status: 'failed' })).not.toContain('cursor');
    expect(historyHref({ status: 'failed', cursor: 'abc' })).toContain('cursor=abc');
  });
});
