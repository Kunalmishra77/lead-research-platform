import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { LeadValue, LeadView } from '../types';
import { PrunedNote } from './pruned-note';

function lead(over: Partial<LeadView> = {}): LeadView {
  return {
    id: 'l1',
    companyId: 'c1',
    status: 'new',
    createdAt: '2026-10-01T00:00:00.000Z',
    name: 'Vanarshala',
    domain: null,
    city: null,
    country: null,
    address: null,
    phone: null,
    googlePlaceId: null,
    exportedAt: null,
    values: [],
    ...over,
  };
}

const value: LeadValue = {
  field: 'email',
  value: 'a@b.test',
  source: 'overture',
  sourceUrl: 'https://example.test',
  observedAt: '2026-09-23T00:00:00.000Z',
  method: 'provider',
  derivation: 'found',
  confidence: 0.85,
};

describe('the pruned note', () => {
  it('explains a row whose details were cleared after export', () => {
    // Without it the row is a name and six dashes, which reads as a lead we failed to enrich.
    render(<PrunedNote lead={lead({ exportedAt: '2026-10-12T00:00:00.000Z', values: [] })} />);
    expect(screen.getByText(/Details cleared after export/)).toBeInTheDocument();
    expect(screen.getByText(/downloaded file/)).toBeInTheDocument();
  });

  it('says nothing about a lead that still has its values', () => {
    // Exported but inside the grace window: nothing is missing, so there is nothing to explain.
    const { container } = render(
      <PrunedNote lead={lead({ exportedAt: '2026-10-12T00:00:00.000Z', values: [value] })} />,
    );
    expect(container.textContent).toBe('');
  });

  it('says nothing about a lead nobody has exported', () => {
    const { container } = render(<PrunedNote lead={lead({ exportedAt: null, values: [] })} />);
    expect(container.textContent).toBe('');
  });
});
