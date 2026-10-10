import type { LeadValue } from './types';

export type ProvenanceKind = 'found' | 'derived' | 'ai' | 'user';

/**
 * Which of the four kinds a value is (docs/09: found vs derived vs AI is always distinguishable;
 * CLAUDE.md: no value without provenance).
 *
 * A plain module with no `'use client'`, because this is arithmetic on a value and both a Server
 * and a Client Component need it. It used to live in `provenance.tsx`, which is a client module,
 * and `SocialsCell` — a Server Component — called it directly. That builds and type-checks fine
 * and fails at request time with "Attempted to call kindOf() from the server but kindOf is on the
 * client", which the browser then shows as a bare digest. It only fired for leads that had a
 * social profile, so it reached production behind a grid that had never had a row in it.
 *
 * Worth knowing: `@testing-library/react` renders everything as client, so no test can catch
 * this. The boundary exists only in a real build.
 */
export function kindOf(value: LeadValue): ProvenanceKind {
  if (value.method === 'ai') return 'ai';
  if (value.derivation === 'derived_pattern') return 'derived';
  if (value.method === 'user') return 'user';
  return 'found';
}
