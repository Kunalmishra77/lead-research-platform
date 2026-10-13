import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AdminTable } from './admin-table';
import { formatBytes, formatDateTime, formatShare } from './format';

describe('formatDateTime', () => {
  it('formats ISO timestamps in UTC and handles missing values', () => {
    expect(formatDateTime('2026-09-18T09:36:05.464Z')).toBe('2026-09-18 09:36 UTC');
    expect(formatDateTime(null)).toBe('—');
    expect(formatDateTime('not a date')).toBe('—');
  });
});

describe('formatBytes', () => {
  it('uses the decimal units a disk quota is sold in', () => {
    // ADR-0019 budgets the raw store against a 1 GB free plan and a 134 GB disk, so a panel
    // showing GiB would disagree with the numbers the decision was made on.
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(900)).toBe('900 B');
    // One decimal only below 10, so a small page reads as `1.5 kB` and a big store as `660 MB`
    // rather than `660.0 MB`.
    expect(formatBytes(1_500)).toBe('1.5 kB');
    expect(formatBytes(30_000)).toBe('30 kB');
    expect(formatBytes(660_000_000)).toBe('660 MB');
    expect(formatBytes(134_000_000_000)).toBe('134 GB');
  });

  it('never shows a negative or a fraction of a byte', () => {
    expect(formatBytes(-1)).toBe('0 B');
  });
});

describe('formatShare', () => {
  it('is an em dash when there is no whole, not zero per cent', () => {
    // 0 of 0 is not 0%. A panel printing 0% would read as "we tried and failed every time".
    expect(formatShare(0, 0)).toBe('—');
    expect(formatShare(351, 607)).toBe('58%');
    expect(formatShare(5, 5)).toBe('100%');
  });
});

describe('AdminTable', () => {
  const columns = [{ header: 'Name', cell: (r: { id: string; name: string }) => r.name }];

  it('renders rows and pagination links', () => {
    render(
      <AdminTable
        caption="Orgs"
        columns={columns}
        rows={[{ id: 'a', name: 'Acme' }]}
        nextHref="/admin/orgs?cursor=a"
        firstHref="/admin/orgs"
      />,
    );
    expect(screen.getByRole('cell', { name: 'Acme' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Next page' })).toHaveAttribute(
      'href',
      '/admin/orgs?cursor=a',
    );
    expect(screen.getByRole('link', { name: 'First page' })).toBeInTheDocument();
  });

  it('shows an empty state and no pagination on a single empty page', () => {
    render(
      <AdminTable caption="Orgs" columns={columns} rows={[]} nextHref={null} firstHref={null} />,
    );
    expect(screen.getByText('Nothing here yet.')).toBeInTheDocument();
    expect(screen.queryByRole('link')).toBeNull();
  });
});
