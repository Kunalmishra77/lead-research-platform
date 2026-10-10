import { FILTERABLE_STATUSES } from './types';

const KNOWN_STATUSES = new Set<string>(FILTERABLE_STATUSES);

/** The longest search term the API accepts; anything more is trimmed rather than rejected. */
const MAX_TERM = 200;

export interface HistoryFilters {
  cursor?: string;
  status?: string;
  q?: string;
}

/**
 * The filters a request may carry, with anything the API would refuse left out.
 *
 * A hand-edited query string should narrow nothing rather than break a page somebody is reading:
 * an unknown status is a 400 from the API and a 500 on screen, and neither tells the reader
 * anything they can act on.
 */
export function allowedFilters(filters: HistoryFilters): HistoryFilters {
  const out: HistoryFilters = {};
  if (filters.status && KNOWN_STATUSES.has(filters.status)) out.status = filters.status;
  const term = filters.q?.trim();
  if (term) out.q = term.slice(0, MAX_TERM);
  if (filters.cursor) out.cursor = filters.cursor;
  return out;
}

/**
 * A link to the history with these filters applied.
 *
 * The cursor is only carried when it is passed: page three of "all runs" is not page three of
 * "failed ones", so changing a filter starts again rather than landing on an empty page that
 * reads as "no results".
 */
export function historyHref(filters: HistoryFilters = {}): string {
  const allowed = allowedFilters(filters);
  const params = new URLSearchParams();
  if (allowed.cursor) params.set('cursor', allowed.cursor);
  if (allowed.status) params.set('status', allowed.status);
  if (allowed.q) params.set('q', allowed.q);
  const query = params.toString();
  return query ? `/research/history?${query}` : '/research/history';
}
