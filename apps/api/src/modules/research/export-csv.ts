import type { LeadValue, LeadView } from './research.dto';

/**
 * Turning leads into a CSV someone can open in Excel (docs/14, task 7.1 in part).
 *
 * This is the smallest useful slice of the export engine. docs/14 describes a worker job writing
 * to S3 with a signed URL, chunked at 5,000 rows, resumable from a cursor — all of which earns its
 * keep for a saved filter spanning a hundred thousand leads, and none of which does for one job's
 * two hundred. So the API streams this one synchronously, and the engine arrives with the phase
 * that needs it. XLSX, NDJSON, splitting at a million rows and gzip are deferred for the same
 * reason and for no other.
 *
 * **Formula-injection escaping is not optional** (docs/10). A cell beginning `=`, `+`, `-`, `@`,
 * tab or carriage return is a formula to Excel, Sheets and LibreOffice, and the values in here
 * came off strangers' websites. `=cmd|'/c calc'!A1` in a company's name field is a working attack
 * on whoever opens the file, and it is our file. The cell is prefixed with an apostrophe, which
 * every spreadsheet reads as "this is text" and strips on display.
 */

/** A leading apostrophe makes a spreadsheet treat the cell as text (docs/10). */
const FORMULA_LEAD = /^[=+\-@\t\r]/;

/** RFC 4180: a field containing any of these must be quoted, and its quotes doubled. */
const MUST_QUOTE = /[",\r\n]/;

/**
 * Excel reads a CSV as the system codepage unless a BOM says otherwise, which turns every
 * Devanagari name and every `₹` into mojibake. docs/14 asks for it by name.
 */
export const UTF8_BOM = '﻿';

/** RFC 4180 line ending. */
const CRLF = '\r\n';

export type ColumnPreset = 'basic' | 'sources';

/**
 * The columns a CSV leads with, in the order a person reading it wants them: who they are, how to
 * reach them, then where they are. Anything stored but not listed here is appended afterwards in
 * alphabetical order, so a field added next month still exports -- a value stored and then dropped
 * from the export is worse than one in an unexpected column.
 */
const LEADING_FIELDS: readonly { field: string; header: string }[] = [
  { field: 'name', header: 'Name' },
  { field: 'email', header: 'Email' },
  { field: 'phone', header: 'Phone' },
  { field: 'website', header: 'Website' },
  { field: 'category', header: 'Category' },
  { field: 'address', header: 'Address' },
  { field: 'city', header: 'City' },
  { field: 'state', header: 'State' },
  { field: 'postal_code', header: 'Postcode' },
  { field: 'country', header: 'Country' },
  { field: 'rating', header: 'Rating' },
  { field: 'review_count', header: 'Reviews' },
  { field: 'instagram', header: 'Instagram' },
  { field: 'facebook', header: 'Facebook' },
  { field: 'linkedin', header: 'LinkedIn' },
  { field: 'whatsapp', header: 'WhatsApp' },
  { field: 'opening_hours', header: 'Opening hours' },
  { field: 'description', header: 'Description' },
];

/** `lead_id` first: an export is only re-importable if each row says which lead it is. */
const ID_HEADER = 'lead_id';

export function escapeCell(raw: unknown): string {
  const text = stringify(raw);
  if (text === '') return '';
  const guarded = FORMULA_LEAD.test(text) ? `'${text}` : text;
  return MUST_QUOTE.test(guarded) ? `"${guarded.replaceAll('"', '""')}"` : guarded;
}

function stringify(raw: unknown): string {
  if (raw === null || raw === undefined) return '';
  if (typeof raw === 'string') return raw;
  if (typeof raw === 'number' || typeof raw === 'boolean') return String(raw);
  if (Array.isArray(raw)) return raw.map(stringify).join(' | ');
  if (typeof raw === 'object') {
    const geo = raw as { lat?: number; lng?: number };
    if (typeof geo.lat === 'number' && typeof geo.lng === 'number') {
      return `${geo.lat},${geo.lng}`;
    }
    return JSON.stringify(raw);
  }
  // Only a symbol, a function or a bigint reaches here, and none of them belongs in a field
  // value. Empty rather than String(), which would put "[object Object]" or a stack-shaped string
  // into somebody's spreadsheet; an empty cell is at least honest about knowing nothing.
  return '';
}

/**
 * The fields to export, in a stable order: the declared ones first, then whatever else these
 * leads carry.
 *
 * Derived from the rows rather than fixed, because a field nobody declared is still something the
 * customer paid to collect. Stable within that, because a CSV people diff between two runs is
 * useless if its columns move.
 */
export function fieldsFor(leads: readonly LeadView[]): { field: string; header: string }[] {
  const present = new Set(leads.flatMap((lead) => lead.values.map((v) => v.field)));
  const declared = LEADING_FIELDS.filter((c) => present.has(c.field));
  const extra = [...present]
    .filter((field) => !LEADING_FIELDS.some((c) => c.field === field))
    .sort()
    .map((field) => ({ field, header: field.replaceAll('_', ' ') }));
  return [...declared, ...extra];
}

export function headerRow(columns: readonly { header: string }[], preset: ColumnPreset): string[] {
  const out = [ID_HEADER];
  for (const column of columns) {
    out.push(column.header);
    if (preset === 'sources') {
      // docs/14's "With sources" preset: source_url and observed_at per field. Next to the value
      // rather than in a block at the end, so a reader checking one cell does not have to count
      // columns to find its evidence.
      out.push(`${column.header} source`, `${column.header} observed at`);
    }
  }
  return out;
}

export function leadRow(
  lead: LeadView,
  columns: readonly { field: string }[],
  preset: ColumnPreset,
): string[] {
  const byField = new Map(lead.values.map((v) => [v.field, v]));
  const out = [lead.id];
  for (const column of columns) {
    const value = byField.get(column.field);
    out.push(stringify(value?.value));
    if (preset === 'sources') {
      out.push(value?.sourceUrl ?? '', value?.observedAt ?? '');
    }
  }
  return out;
}

/** One CSV line, each cell escaped. */
export function csvLine(cells: readonly string[]): string {
  return cells.map(escapeCell).join(',') + CRLF;
}

/**
 * The whole file, for a page of leads. Yielded line by line so the caller can stream it: the rows
 * are already in memory here, but the string never has to be.
 */
export function* csvLines(
  leads: readonly LeadView[],
  preset: ColumnPreset = 'basic',
): Generator<string> {
  const columns = fieldsFor(leads);
  yield UTF8_BOM + csvLine(headerRow(columns, preset));
  for (const lead of leads) {
    yield csvLine(leadRow(lead, columns, preset));
  }
}

/** A filename a person can find again: the job it came from and the day it was taken. */
export function exportFilename(jobId: string, now: Date = new Date()): string {
  return `leads-${jobId.slice(0, 8)}-${now.toISOString().slice(0, 10)}.csv`;
}

export type { LeadValue };
