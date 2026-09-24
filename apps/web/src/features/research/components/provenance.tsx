'use client';

import type { LeadValue } from '../types';

/**
 * Where a value came from, on the value itself (docs/09: found vs derived vs AI is always
 * distinguishable; CLAUDE.md: no value without provenance).
 *
 * The shape carries the meaning, not just the colour — a filled dot for something a source
 * stated, a hollow one for something a rule worked out, a sparkle for something a model inferred
 * — so it survives greyscale and colour blindness. The title and the screen-reader text say it in
 * words either way.
 */
export function ProvenanceDot({ value }: { value: LeadValue }) {
  const kind = kindOf(value);
  const label = LABELS[kind];
  return (
    <span className="inline-flex items-center" title={`${label} · ${value.source}`}>
      <span aria-hidden className={DOT[kind]} />
      <span className="sr-only">{label}</span>
    </span>
  );
}

/** One cell: the value, its provenance, and a link to the evidence. */
export function ValueCell({ value }: { value: LeadValue | undefined }) {
  if (!value) return <span className="text-muted-foreground">—</span>;
  return (
    <span className="inline-flex items-center gap-1.5">
      <ProvenanceDot value={value} />
      <span>{format(value.value)}</span>
    </span>
  );
}

export function kindOf(value: LeadValue): ProvenanceKind {
  if (value.method === 'ai') return 'ai';
  if (value.derivation === 'derived_pattern') return 'derived';
  if (value.method === 'user') return 'user';
  return 'found';
}

export type ProvenanceKind = 'found' | 'derived' | 'ai' | 'user';

const LABELS: Record<ProvenanceKind, string> = {
  found: 'Found in a source',
  derived: 'Worked out by a rule',
  ai: 'Inferred by AI',
  user: 'Entered by someone here',
};

const DOT: Record<ProvenanceKind, string> = {
  found: 'size-1.5 rounded-full bg-foreground/60',
  derived: 'size-1.5 rounded-full border border-dashed border-foreground/60',
  ai: 'size-1.5 rounded-full bg-ai',
  user: 'size-1.5 rounded-full border border-info bg-info/30',
};

function format(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return value.toLocaleString('en-IN');
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (Array.isArray(value)) return value.map(format).join(', ');
  if (typeof value === 'object') {
    const geo = value as { lat?: number; lng?: number };
    if (typeof geo.lat === 'number' && typeof geo.lng === 'number') {
      return `${geo.lat.toFixed(4)}, ${geo.lng.toFixed(4)}`;
    }
  }
  return JSON.stringify(value);
}

/** A legend, so the dots mean something the first time someone sees them. */
export function ProvenanceLegend({ className }: { className?: string }) {
  return (
    <ul
      className={`text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs ${className ?? ''}`}
    >
      {(['found', 'derived', 'ai'] as const).map((kind) => (
        <li key={kind} className="inline-flex items-center gap-1.5">
          <span aria-hidden className={DOT[kind]} />
          {LABELS[kind]}
        </li>
      ))}
    </ul>
  );
}
