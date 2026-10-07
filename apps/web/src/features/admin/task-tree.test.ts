import { describe, expect, it } from 'vitest';

import type { AdminTask } from './api';
import { inputSummary, toTaskTree } from './task-tree';

const task = (id: string, parentTaskId: string | null = null): AdminTask => ({
  id,
  parentTaskId,
  type: 'discovery.places_text_search',
  status: 'completed',
  attempts: 1,
  creditBudget: 3,
  costMicros: 0,
  errorClass: null,
  startedAt: null,
  finishedAt: null,
  durationMs: null,
  input: {},
});

describe('toTaskTree', () => {
  it('nests a fan-out under the task that planned it', () => {
    const roots = toTaskTree([task('plan'), task('a', 'plan'), task('b', 'plan')]);
    expect(roots).toHaveLength(1);
    expect(roots[0]?.task.id).toBe('plan');
    expect(roots[0]?.children.map((c) => c.task.id)).toEqual(['a', 'b']);
  });

  it('promotes a task whose parent is off the end of the page', () => {
    // admin_job_tasks caps at 2000 rows, so a large job can lose its parent. Dropping the children
    // would make the job look emptier than it was, which is the opposite of the point.
    const roots = toTaskTree([task('a', 'missing'), task('b', 'missing')]);
    expect(roots.map((r) => r.task.id)).toEqual(['a', 'b']);
    expect(roots.every((r) => r.children.length === 0)).toBe(true);
  });

  it('does not recurse for ever on a task that is its own parent', () => {
    const roots = toTaskTree([task('self', 'self')]);
    expect(roots).toHaveLength(1);
    expect(roots[0]?.children).toEqual([]);
  });

  it('returns nothing for a job that was never planned', () => {
    expect(toTaskTree([])).toEqual([]);
  });
});

describe('inputSummary', () => {
  it('summarises the scalars that say what was searched for', () => {
    expect(inputSummary({ query: 'dental clinic in Delhi', page: 2 })).toBe(
      'query=dental clinic in Delhi · page=2',
    );
  });

  it('skips nested values rather than printing them raw', () => {
    expect(inputSummary({ area: { bbox: [1, 2, 3, 4] }, query: 'dentist' })).toBe('query=dentist');
  });

  it('is null when there is nothing worth a line', () => {
    expect(inputSummary({})).toBeNull();
    expect(inputSummary({ area: { bbox: [] } })).toBeNull();
  });
});
