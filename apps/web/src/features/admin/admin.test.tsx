import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AdminTable } from './admin-table';
import { formatDateTime } from './format';

describe('formatDateTime', () => {
  it('formats ISO timestamps in UTC and handles missing values', () => {
    expect(formatDateTime('2026-09-18T09:36:05.464Z')).toBe('2026-09-18 09:36 UTC');
    expect(formatDateTime(null)).toBe('—');
    expect(formatDateTime('not a date')).toBe('—');
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
