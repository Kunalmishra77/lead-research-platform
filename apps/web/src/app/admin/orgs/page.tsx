import type { Metadata } from 'next';

import { AdminTable, type Column } from '@/features/admin/admin-table';
import { type AdminOrg, listOrgs } from '@/features/admin/api';
import { formatDateTime } from '@/features/admin/format';

export const metadata: Metadata = { title: 'Organizations' };

const columns: Column<AdminOrg>[] = [
  { header: 'Name', cell: (o) => <span className="font-medium">{o.name}</span> },
  { header: 'Slug', cell: (o) => <code className="text-xs">{o.slug}</code> },
  { header: 'Plan', cell: (o) => o.plan },
  { header: 'Region', cell: (o) => o.region },
  { header: 'Members', cell: (o) => o.memberCount, numeric: true },
  { header: 'Created', cell: (o) => formatDateTime(o.createdAt) },
];

export default async function AdminOrgsPage({
  searchParams,
}: {
  searchParams: Promise<{ cursor?: string | string[] }>;
}) {
  const { cursor } = await searchParams;
  const page = await listOrgs(cursor);
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold tracking-tight">Organizations</h1>
      <AdminTable
        caption="Organizations"
        columns={columns}
        rows={page.items}
        nextHref={page.nextCursor ? `/admin/orgs?cursor=${page.nextCursor}` : null}
        firstHref={cursor ? '/admin/orgs' : null}
      />
    </div>
  );
}
