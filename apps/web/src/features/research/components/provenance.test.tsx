import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { LeadValue } from '../types';
import { kindOf, ValueCell } from './provenance';

function value(over: Partial<LeadValue> = {}): LeadValue {
  return {
    field: 'phone',
    value: '+911234567890',
    source: 'google_places',
    sourceUrl: 'https://maps.google.com/?q=place_id:x',
    observedAt: '2026-09-24T10:00:00.000Z',
    method: 'api',
    derivation: 'found',
    confidence: 0.85,
    ...over,
  };
}

/**
 * Found, derived and inferred are different promises (docs/09, CLAUDE.md), and a user acting on
 * a phone number needs to know which one they are looking at.
 */
describe('provenance', () => {
  it('tells apart what a source said, what a rule worked out, and what a model inferred', () => {
    expect(kindOf(value())).toBe('found');
    expect(kindOf(value({ derivation: 'derived_pattern' }))).toBe('derived');
    expect(kindOf(value({ method: 'ai', derivation: null }))).toBe('ai');
    expect(kindOf(value({ method: 'user', derivation: 'user' }))).toBe('user');
  });

  it('says which it is in words, not only in colour', () => {
    // Colour is never the only signal (docs/09 accessibility).
    render(<ValueCell value={value({ derivation: 'derived_pattern' })} />);
    expect(screen.getByText('Worked out by a rule')).toBeInTheDocument();
  });

  it('shows a dash rather than an empty cell when there is no value', () => {
    render(<ValueCell value={undefined} />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('formats the shapes the API actually sends', () => {
    const { rerender } = render(<ValueCell value={value({ field: 'rating', value: 4.5 })} />);
    expect(screen.getByText('4.5')).toBeInTheDocument();

    rerender(<ValueCell value={value({ field: 'review_count', value: 1234 })} />);
    expect(screen.getByText('1,234')).toBeInTheDocument();

    rerender(<ValueCell value={value({ field: 'geo', value: { lat: 28.6139, lng: 77.209 } })} />);
    expect(screen.getByText('28.6139, 77.2090')).toBeInTheDocument();
  });
});
