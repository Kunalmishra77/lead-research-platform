import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { csvLines } from './export-csv';
import { CDLA_PERMISSIVE_2_0, noticeLines } from './export-notice';
import type { LeadView } from './research.dto';

/** The repository's copy, which the constant in the code has to match exactly. */
const VENDORED = resolve(
  import.meta.dirname,
  '..',
  '..',
  '..',
  '..',
  '..',
  'docs',
  'licenses',
  'CDLA-Permissive-2.0.md',
);

function lead(id: string, sources: string[]): LeadView {
  return {
    id,
    companyId: `c-${id}`,
    name: `Gym ${id}`,
    values: sources.map((source) => ({
      field: 'phone',
      value: '+911141000000',
      source,
      sourceUrl: 'https://example.test/p',
      observedAt: '2026-09-23T00:00:00.000Z',
      method: 'provider',
      derivation: 'found',
      confidence: 0.85,
    })),
  } as unknown as LeadView;
}

describe('export licence notice', () => {
  it('matches the licence text checked into the repository', () => {
    // The constant exists so an export cannot fail on a path inside a container, and so the
    // licence cannot be left out of an image by a forgotten COPY. The cost of that is a second
    // copy, and the only thing keeping two copies honest is this.
    const vendored = readFileSync(VENDORED, 'utf8');
    const stripped = vendored
      .replace(/^# /gm, '')
      .replace(/^## /gm, '')
      .replace(/\r\n/g, '\n')
      .trim();
    expect(CDLA_PERMISSIVE_2_0.replace(/\r\n/g, '\n').trim()).toBe(stripped);
  });

  it('carries the full agreement when the data came from Overture', () => {
    // CDLA-Permissive-2.0 section 2.1: sharing the Data obliges us to make the agreement text
    // available with it. A link on our own site is not with a file somebody downloaded.
    const lines = noticeLines(['overture']);
    expect(lines.join('\n')).toContain('Community Data License Agreement');
    expect(lines.join('\n')).toContain('2.1. A Data Recipient may share Data');
  });

  it('attributes OpenStreetMap without reciting ODbL', () => {
    // ODbL asks to be credited, not quoted. Pasting its text as well would bury the obligation
    // that does apply under one that does not.
    const text = noticeLines(['osm']).join('\n');
    expect(text).toContain('OpenStreetMap contributors');
    expect(text).not.toContain('Open Database License');
  });

  it('says nothing at all when nothing in the file is licensed', () => {
    // A workspace exporting what our own crawler read off companies' websites owes nobody a
    // notice, and a licence block on that file is noise that teaches people to skip them.
    expect(noticeLines([])).toEqual([]);
    expect(noticeLines(['some_future_source'])).toEqual([]);
  });

  it('names each source once however many leads came from it', () => {
    const text = noticeLines(['overture', 'overture', 'osm', 'overture']).join('\n');
    expect(text.match(/Overture Maps/g)).toHaveLength(1);
    expect(text.match(/OpenStreetMap contributors/g)).toHaveLength(1);
  });

  it('follows the data, behind a blank row', () => {
    // The order matters both ways: a reader wants the leads first, and an importer that stops
    // at the first blank row gets every lead and none of this.
    const rows = [...csvLines([lead('1', ['overture'])])];
    const blank = rows.findIndex((r) => r.trim() === '');
    expect(blank).toBeGreaterThan(1);
    expect(rows.slice(0, blank).join()).toContain('911141000000');
    expect(rows.slice(blank).join()).toContain('Community Data License Agreement');
  });

  it('quotes every notice line, so a comma in the licence cannot shift a column', () => {
    const rows = [...csvLines([lead('1', ['overture'])])];
    const notice = rows.slice(rows.findIndex((r) => r.trim() === ''));
    for (const row of notice) {
      const body = row.replace(/\r\n$/, '');
      if (body === '') continue;
      expect(body.startsWith('"') || !body.includes(',')).toBe(true);
    }
  });

  it('adds nothing to an export whose leads carry no licensed source', () => {
    const rows = [...csvLines([lead('1', ['website'])])];
    expect(rows.join()).toContain("business's own website");
    expect(rows.join()).not.toContain('Community Data License Agreement');
  });
});
