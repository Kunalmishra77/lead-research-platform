import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { GeographyAttribution, SourceAttribution } from './attribution';

/**
 * These are conditions of using the data, not styling. Google Places content must carry visible
 * Google attribution wherever it is shown (ADR-0011) and seeded geography must credit
 * OpenStreetMap under ODbL (docs/08) — so the tests are about whether the credit appears at all,
 * and about it not appearing for sources that did not contribute.
 */
describe('attribution', () => {
  it('credits Google when a Places value is on the page', () => {
    render(<SourceAttribution sources={['google_places']} />);
    expect(screen.getByText(/from Google/i)).toBeInTheDocument();
  });

  it('credits OpenStreetMap when seeded geography is shown, with a link to the licence', () => {
    render(<GeographyAttribution />);
    const link = screen.getByRole('link', { name: /OpenStreetMap contributors/i });
    expect(link).toHaveAttribute('href', 'https://www.openstreetmap.org/copyright');
    expect(link).toHaveTextContent('ODbL');
  });

  it('credits both when a page shows both', () => {
    render(<SourceAttribution geography sources={['google_places', 'serp']} />);
    expect(screen.getByText(/from Google/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /OpenStreetMap/i })).toBeInTheDocument();
  });

  it('says nothing when nothing on the page needs crediting', () => {
    // A page of values from sources with no attribution requirement must not imply Google
    // supplied them.
    const { container } = render(<SourceAttribution sources={['serp']} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('does not credit Google for a page with no Places values', () => {
    render(<SourceAttribution geography sources={['serp']} />);
    expect(screen.queryByText(/from Google/i)).not.toBeInTheDocument();
  });
});
