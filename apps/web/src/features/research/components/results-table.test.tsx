import { describe, expect, it } from 'vitest';

import { emptyMessage } from './results-table';

/**
 * An empty grid is not one situation. The job may still be running, it may have found nothing,
 * or it may have found businesses and delivered none of them — and the counters above the grid
 * are visible at the same time, so a message that disagrees with them is a bug in itself.
 */
describe('emptyMessage', () => {
  it('promises more while the job is still working', () => {
    expect(emptyMessage({ finished: false, candidates: 0 })).toMatch(/appear here as the search/);
  });

  it('suggests a wider search when the run genuinely saw nothing', () => {
    expect(emptyMessage({ finished: true, candidates: 0 })).toMatch(/widening the area/);
  });

  it('does not claim nothing was found while the counter above says twelve', () => {
    // The bug this is here for: "found no businesses" printed under "Businesses seen 12".
    const message = emptyMessage({ finished: true, candidates: 12 });
    expect(message).toContain('12');
    expect(message).not.toMatch(/found no businesses/);
  });

  it('treats an unknown count as nothing found rather than inventing a number', () => {
    expect(emptyMessage({ finished: true, candidates: undefined })).toMatch(/widening the area/);
  });
});
