import type { Metadata } from 'next';

import { AdminTable, type Column } from '@/features/admin/admin-table';
import { type AdminUser, listUsers } from '@/features/admin/api';
import { formatDateTime } from '@/features/admin/format';

export const metadata: Metadata = { title: 'Users' };

const columns: Column<AdminUser>[] = [
  { header: 'Email', cell: (u) => <span className="font-medium">{u.email ?? '—'}</span> },
  { header: 'Status', cell: (u) => (u.emailConfirmedAt ? 'Confirmed' : 'Unconfirmed') },
  { header: 'Staff', cell: (u) => (u.isPlatformStaff ? 'Yes' : '') },
  { header: 'Orgs', cell: (u) => u.orgCount, numeric: true },
  { header: 'Last sign-in', cell: (u) => formatDateTime(u.lastSignInAt) },
  { header: 'Created', cell: (u) => formatDateTime(u.createdAt) },
];

export default async function AdminUsersPage({
  searchParams,
}: {
  searchParams: Promise<{ cursor?: string | string[] }>;
}) {
  const { cursor } = await searchParams;
  const page = await listUsers(cursor);
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold tracking-tight">Users</h1>
      <AdminTable
        caption="Users"
        columns={columns}
        rows={page.items}
        nextHref={page.nextCursor ? `/admin/users?cursor=${page.nextCursor}` : null}
        firstHref={cursor ? '/admin/users' : null}
      />
    </div>
  );
}
