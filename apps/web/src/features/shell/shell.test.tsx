import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { SidebarNav } from './sidebar';
import { WorkspaceSwitcher } from './workspace-switcher';

vi.mock('next/navigation', () => ({
  usePathname: () => '/dashboard',
}));
vi.mock('@/features/workspace/actions', () => ({ selectWorkspace: vi.fn() }));

describe('SidebarNav', () => {
  it('renders docs/09 items, marks the active page and disables later-phase items', () => {
    render(<SidebarNav showDeveloper={false} />);
    const dashboard = screen.getByRole('link', { name: 'Dashboard' });
    expect(dashboard).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByRole('link', { name: /New Research/ })).toBeNull();
    expect(screen.getByText('New Research').closest('[aria-disabled="true"]')).not.toBeNull();
    expect(screen.queryByRole('link', { name: 'Job pipeline check' })).toBeNull();
  });

  it('shows developer tools only when asked', () => {
    render(<SidebarNav showDeveloper />);
    expect(screen.getByRole('link', { name: 'Job pipeline check' })).toBeInTheDocument();
  });
});

describe('WorkspaceSwitcher', () => {
  it('lists every membership with the active workspace selected', () => {
    render(
      <WorkspaceSwitcher
        activeWorkspaceId="w2"
        memberships={[
          {
            orgId: 'o1',
            orgName: 'Acme',
            orgSlug: 'acme',
            workspaceId: 'w1',
            workspaceName: 'Default',
            role: 'owner',
          },
          {
            orgId: 'o2',
            orgName: 'Beta',
            orgSlug: 'beta',
            workspaceId: 'w2',
            workspaceName: 'Sales',
            role: 'member',
          },
        ]}
      />,
    );
    const select = screen.getByRole('combobox', { name: 'Workspace' });
    expect(select).toHaveValue('w2');
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Acme · Default',
      'Beta · Sales',
    ]);
  });
});
