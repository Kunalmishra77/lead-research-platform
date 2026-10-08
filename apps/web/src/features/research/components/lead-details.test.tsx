import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { LeadValue, LeadView } from '../types';
import { LeadDetails } from './lead-details';
import { SocialsCell } from './socials-cell';

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

function lead(values: LeadValue[]): LeadView {
  return {
    id: 'lead-1',
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

const EMAIL = value({
  field: 'email',
  value: 'info@clinic.example',
  source: 'website',
  sourceUrl: 'https://clinic.example/contact',
  method: 'crawl',
  confidence: 0.75,
});

/**
 * The drawer is where "every value says where it came from" stops being a claim. A link nobody
 * can follow is marketing, so these tests are mostly about the evidence being reachable.
 */
describe('LeadDetails', () => {
  it('shows a value with its source, date and confidence, linked to the page it came from', () => {
    render(<LeadDetails lead={lead([EMAIL])} />);
    expect(screen.getByText('info@clinic.example')).toBeInTheDocument();
    const evidence = screen.getByRole('link', { name: /website · 2026-09-25 · 75%/ });
    expect(evidence).toHaveAttribute('href', 'https://clinic.example/contact');
  });

  it('says where a value came from even when there is no page to link to', () => {
    render(<LeadDetails lead={lead([value({ field: 'email', sourceUrl: '' })])} />);
    expect(screen.getByText(/google_places · 2026-09-25 · 85%/)).toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('groups fields the way someone working a lead reads them', () => {
    render(
      <LeadDetails
        lead={lead([
          EMAIL,
          value({ field: 'city', value: 'New Delhi' }),
          value({ field: 'instagram', value: 'clinicdelhi' }),
        ])}
      />,
    );
    expect(screen.getByText('Contact')).toBeInTheDocument();
    expect(screen.getByText('Location')).toBeInTheDocument();
    expect(screen.getByText('Profiles')).toBeInTheDocument();
    // Nothing in the Business group, so no empty heading for it.
    expect(screen.queryByText('Business')).not.toBeInTheDocument();
  });

  it('shows a field it has no group for rather than dropping it', () => {
    // A value stored and then hidden is worse than one under an ugly heading: the second is
    // fixable by reading this list, the first is invisible.
    render(<LeadDetails lead={lead([value({ field: 'employee_band', value: '11-50' })])} />);
    expect(screen.getByText('Other')).toBeInTheDocument();
    expect(screen.getByText('employee band')).toBeInTheDocument();
    expect(screen.getByText('11-50')).toBeInTheDocument();
  });

  it('says so plainly when there is nothing stored yet', () => {
    render(<LeadDetails lead={lead([])} />);
    expect(screen.getByText(/Nothing stored for this business yet/)).toBeInTheDocument();
  });

  it('renders a list value readably rather than as JSON', () => {
    render(
      <LeadDetails
        lead={lead([value({ field: 'opening_hours', value: ['Mo-Sa 09:00-19:00'] })])}
      />,
    );
    expect(screen.getByText('Mo-Sa 09:00-19:00')).toBeInTheDocument();
  });
});

/**
 * Handles are stored, not crawled (docs/08 row 50): the link is built from the handle rather than
 * from a URL we never visited.
 */
describe('SocialsCell', () => {
  it('builds a profile link from the stored handle', () => {
    render(<SocialsCell lead={lead([value({ field: 'instagram', value: 'clinicdelhi' })])} />);
    expect(screen.getByRole('link', { name: 'Instagram: clinicdelhi' })).toHaveAttribute(
      'href',
      'https://instagram.com/clinicdelhi',
    );
  });

  it("keeps LinkedIn's company or person prefix, which is not guessable from a name", () => {
    render(
      <SocialsCell lead={lead([value({ field: 'linkedin', value: 'company/clinic-delhi' })])} />,
    );
    expect(screen.getByRole('link', { name: /LinkedIn/ })).toHaveAttribute(
      'href',
      'https://linkedin.com/company/clinic-delhi',
    );
  });

  it('turns a WhatsApp number into a wa.me link', () => {
    render(<SocialsCell lead={lead([value({ field: 'whatsapp', value: '+91 98765 43210' })])} />);
    expect(screen.getByRole('link', { name: /WhatsApp/ })).toHaveAttribute(
      'href',
      'https://wa.me/919876543210',
    );
  });

  it('shows nothing rather than an empty box when a lead has no profiles', () => {
    render(<SocialsCell lead={lead([EMAIL])} />);
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('names the platform and the provenance for a screen reader and on hover', () => {
    render(
      <SocialsCell
        lead={lead([
          value({ field: 'facebook', value: 'clinicdelhi', source: 'website', method: 'crawl' }),
        ])}
      />,
    );
    const link = screen.getByRole('link', { name: 'Facebook: clinicdelhi' });
    expect(link).toHaveAttribute('title', expect.stringContaining('found in a source'));
    expect(link).toHaveAttribute('title', expect.stringContaining('website'));
  });
});
