import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Share, sourceName } from './overview';

/**
 * A share bar states a proportion, and a proportion is the easiest thing on a dashboard to get
 * quietly wrong: an empty workspace divides by zero, and a bar whose length disagrees with the
 * number beside it is worse than no bar.
 */
describe('Share', () => {
  it('writes the count and the percentage, and draws the bar to match', () => {
    const { container } = render(<Share label="Email" total={20} value={7} />);
    expect(screen.getByText(/^7$/)).toBeInTheDocument();
    expect(screen.getByText('(35%)')).toBeInTheDocument();
    expect(container.querySelector<HTMLElement>('.bg-primary')?.style.width).toBe('35%');
  });

  it('shows nothing rather than NaN for an empty workspace', () => {
    // The first thing a new account sees. `0/0` is the one input this is guaranteed to get.
    const { container } = render(<Share label="Email" total={0} value={0} />);
    expect(screen.getByText('(0%)')).toBeInTheDocument();
    expect(container.querySelector<HTMLElement>('.bg-primary')?.style.width).toBe('0%');
  });

  it('is complete rather than rounded past the end', () => {
    const { container } = render(<Share label="Phone" total={22} value={22} />);
    expect(container.querySelector<HTMLElement>('.bg-primary')?.style.width).toBe('100%');
  });
});

describe('sourceName', () => {
  it('names the sources a person reads, not the keys the code uses', () => {
    expect(sourceName('google_places')).toBe('Google Maps');
    expect(sourceName('website')).toBe("The company's own site");
  });

  it('makes an unknown key readable rather than hiding it', () => {
    // A source added later should appear on the dashboard the day it stores its first value,
    // without anyone remembering to add it here.
    expect(sourceName('open_street_map')).toBe('open street map');
  });
});
