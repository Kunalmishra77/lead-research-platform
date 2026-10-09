import { describe, expect, it } from 'vitest';

import type { JobProgress } from '../types';
import { deliveredNote } from './job-live';

/**
 * A job delivers every lead it finds and is charged only for the ones the workspace did not
 * already hold (ADR-0015). The two numbers are equal on a first run and wildly different on a
 * repeat, and the note is what stops the second case reading as a failure.
 */
describe('deliveredNote', () => {
  function progress(over: Partial<JobProgress>): JobProgress {
    return { candidates: 12, leads: 12, new_leads: 12, values: 179, ...over };
  }

  it('reassures a repeat search that found everything and charged nothing', () => {
    // The live run this was written for: twelve clinics, all delivered a fortnight earlier.
    expect(deliveredNote(progress({ new_leads: 0 }))).toBe('all already in this workspace');
  });

  it('says so plainly when every lead is new', () => {
    expect(deliveredNote(progress({ leads: 12, new_leads: 12 }))).toBe('all new to this workspace');
  });

  it('counts the new ones when a run is part old and part new', () => {
    expect(deliveredNote(progress({ leads: 12, new_leads: 5 }))).toBe('5 new to this workspace');
  });

  it('stays quiet when there is nothing to qualify', () => {
    // "0 new" under a zero explains a number nobody was confused by.
    expect(deliveredNote(progress({ leads: 0, new_leads: 0 }))).toBeUndefined();
    expect(deliveredNote(null)).toBeUndefined();
    expect(deliveredNote(undefined)).toBeUndefined();
  });

  it('stays quiet rather than guessing when the worker sent no new-lead count', () => {
    // A job that ran before this counter existed. Printing "12 new" would be an invention.
    expect(deliveredNote({ leads: 12 })).toBeUndefined();
  });
});
