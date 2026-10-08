import { describe, expect, it } from 'vitest';

import {
  csvLine,
  csvLines,
  escapeCell,
  exportFilename,
  fieldsFor,
  headerRow,
  leadRow,
  UTF8_BOM,
} from './export-csv';
import type { LeadValue, LeadView } from './research.dto';

function value(over: Partial<LeadValue>): LeadValue {
  return {
    field: 'name',
    value: 'Clinic Delhi',
    source: 'google_places',
    sourceUrl: 'https://maps.google.com/?q=place_id:x',
    observedAt: '2026-09-25T10:00:00.000Z',
    method: 'api',
    derivation: 'found',
    confidence: 0.85,
    ...over,
  };
}

function lead(values: LeadValue[], id = 'lead-1'): LeadView {
  return {
    id,
    companyId: 'company-1',
    status: 'new',
    createdAt: '2026-10-08T09:00:00.000Z',
    name: 'Clinic Delhi',
    domain: 'clinic.example',
    city: 'New Delhi',
    country: 'IN',
    address: '12 Main Road',
    phone: '+911123456789',
    googlePlaceId: 'place-1',
    values,
  };
}

/**
 * The values in an export came off strangers' websites, and the file is ours. docs/10 requires
 * formula-injection escaping for exactly that reason.
 */
describe('formula injection', () => {
  it.each(["=cmd|'/c calc'!A1", '+1+1', '-1+1', '@SUM(A1)', '\tx', '\rx'])(
    'defuses a cell beginning %j',
    (payload) => {
      const cell = escapeCell(payload);
      // The apostrophe is what every spreadsheet reads as "this is text".
      expect(cell.replace(/^"/, '').startsWith("'")).toBe(true);
    },
  );

  it('leaves an ordinary value alone', () => {
    expect(escapeCell('Clinic Delhi')).toBe('Clinic Delhi');
    expect(escapeCell('info@clinic.example')).toBe('info@clinic.example');
  });

  it('does not mistake a phone number for a formula', () => {
    // `+919876543210` begins with `+`, so it is escaped -- and it has to be, because Excel would
    // otherwise evaluate it. The apostrophe is invisible in the cell.
    expect(escapeCell('+919876543210')).toBe("'+919876543210");
  });
});

describe('RFC 4180 quoting', () => {
  it('quotes a field containing a comma, a quote or a newline', () => {
    expect(escapeCell('Shah, Patel & Co')).toBe('"Shah, Patel & Co"');
    expect(escapeCell('He said "hello"')).toBe('"He said ""hello"""');
    expect(escapeCell('line one\nline two')).toBe('"line one\nline two"');
  });

  it('escapes a value that is both a formula and needs quoting', () => {
    expect(escapeCell('=1,2')).toBe('"\'=1,2"');
  });

  it('writes an empty cell for a missing value', () => {
    expect(escapeCell(null)).toBe('');
    expect(escapeCell(undefined)).toBe('');
    expect(escapeCell('')).toBe('');
  });

  it('ends every line with CRLF', () => {
    expect(csvLine(['a', 'b'])).toBe('a,b\r\n');
  });
});

describe('value formatting', () => {
  it('joins a list with a separator rather than printing JSON', () => {
    // No comma, quote or newline in the joined string, so RFC 4180 does not ask for quotes.
    expect(escapeCell(['Mo-Sa 09:00-19:00', 'Su closed'])).toBe('Mo-Sa 09:00-19:00 | Su closed');
    // One with a comma is quoted, which is what the separator choice avoids for the common case.
    expect(escapeCell(['a,b', 'c'])).toBe('"a,b | c"');
  });

  it('flattens coordinates', () => {
    expect(escapeCell({ lat: 28.5355, lng: 77.391 })).toBe('"28.5355,77.391"');
  });

  it('keeps a number a number so a spreadsheet can sum it', () => {
    expect(escapeCell(4.8)).toBe('4.8');
    expect(escapeCell(214)).toBe('214');
  });
});

describe('columns', () => {
  it('leads with the fields a person reads first and keeps that order', () => {
    const columns = fieldsFor([
      lead([
        value({ field: 'city', value: 'New Delhi' }),
        value({ field: 'email', value: 'info@clinic.example' }),
        value({ field: 'name' }),
      ]),
    ]);
    expect(columns.map((c) => c.field)).toEqual(['name', 'email', 'city']);
  });

  it('exports a field nobody declared rather than dropping it', () => {
    // The customer paid to collect it. A value stored and then missing from the export is worse
    // than one in an unexpected column.
    const columns = fieldsFor([lead([value({ field: 'employee_band', value: '11-50' })])]);
    expect(columns).toEqual([{ field: 'employee_band', header: 'employee band' }]);
  });

  it('omits a column no lead on the page has', () => {
    const columns = fieldsFor([lead([value({ field: 'name' })])]);
    expect(columns.map((c) => c.field)).toEqual(['name']);
  });

  it('puts lead_id first, so an export can be read back', () => {
    const columns = fieldsFor([lead([value({ field: 'name' })])]);
    expect(headerRow(columns, 'basic')).toEqual(['lead_id', 'Name']);
    expect(leadRow(lead([value({ field: 'name' })]), columns, 'basic')).toEqual([
      'lead-1',
      'Clinic Delhi',
    ]);
  });
});

describe('the "with sources" preset', () => {
  it('puts each value next to the page it came from and the date', () => {
    const values = [
      value({
        field: 'email',
        value: 'info@clinic.example',
        sourceUrl: 'https://clinic.example/contact',
        observedAt: '2026-10-08T09:00:00.000Z',
      }),
    ];
    const columns = fieldsFor([lead(values)]);
    expect(headerRow(columns, 'sources')).toEqual([
      'lead_id',
      'Email',
      'Email source',
      'Email observed at',
    ]);
    expect(leadRow(lead(values), columns, 'sources')).toEqual([
      'lead-1',
      'info@clinic.example',
      'https://clinic.example/contact',
      '2026-10-08T09:00:00.000Z',
    ]);
  });

  it('leaves the evidence cells empty for a field this lead does not have', () => {
    const columns = fieldsFor([
      lead([value({ field: 'email' })]),
      lead([value({ field: 'phone' })], 'lead-2'),
    ]);
    const row = leadRow(lead([value({ field: 'email' })]), columns, 'sources');
    // email, its source, its date, then phone's three -- all empty.
    expect(row.slice(4)).toEqual(['', '', '']);
  });
});

describe('the file', () => {
  it('starts with a BOM, or Excel reads every Devanagari name as mojibake', () => {
    const lines = [...csvLines([lead([value({ field: 'name' })])])];
    expect(lines[0]?.startsWith(UTF8_BOM)).toBe(true);
  });

  it('writes a header and one line per lead', () => {
    const lines = [
      ...csvLines([
        lead([value({ field: 'name', value: 'One' })], 'a'),
        lead([value({ field: 'name', value: 'Two' })], 'b'),
      ]),
    ];
    expect(lines).toHaveLength(3);
    expect(lines[1]).toBe('a,One\r\n');
    expect(lines[2]).toBe('b,Two\r\n');
  });

  it('writes just a header when there is nothing to export', () => {
    const lines = [...csvLines([])];
    expect(lines).toEqual([`${UTF8_BOM}lead_id\r\n`]);
  });

  it('names the file after the job and the day it was taken', () => {
    expect(
      exportFilename('01a0d764-92d8-7b55-b31d-dcac2724bb8c', new Date('2026-10-08T12:00:00Z')),
    ).toBe('leads-01a0d764-2026-10-08.csv');
  });
});
