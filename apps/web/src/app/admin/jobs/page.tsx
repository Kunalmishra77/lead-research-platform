import type { Metadata } from 'next';
import Link from 'next/link';

import { AdminTable, type Column } from '@/features/admin/admin-table';
import {
  type AdminJob,
  asJobStatus,
  listJobs,
  RESEARCH_JOB_STATUSES,
  type ResearchJobStatus,
} from '@/features/admin/api';
import { formatDateTime } from '@/features/admin/format';

export const metadata: Metadata = { title: 'Research jobs' };

/** The counters the worker accumulates. Unknown keys are ignored rather than printed raw. */
function progressSummary(progress: Record<string, unknown>): string {
  const of = (key: string) => (typeof progress[key] === 'number' ? progress[key] : 0);
  return `${of('candidates')} seen · ${of('leads')} leads · ${of('values')} values`;
}

const columns: Column<AdminJob>[] = [
  {
    header: 'Job',
    cell: (j) => (
      <Link href={`/admin/jobs/${j.id}`} className="font-medium hover:underline">
        <code className="text-xs">{j.id.slice(0, 8)}</code>
      </Link>
    ),
  },
  { header: 'Org', cell: (j) => j.orgName },
  {
    header: 'Status',
    cell: (j) => (
      <span className="flex flex-col">
        <span>{j.status}</span>
        {j.errorClass ? (
          <span className="text-xs text-muted-foreground">{j.errorClass}</span>
        ) : null}
      </span>
    ),
  },
  { header: 'Depth', cell: (j) => j.depth },
  { header: 'Progress', cell: (j) => progressSummary(j.progress) },
  {
    header: 'Tasks',
    cell: (j) => (j.tasksFailed > 0 ? `${j.tasksTotal} (${j.tasksFailed} failed)` : j.tasksTotal),
    numeric: true,
  },
  {
    header: 'Credits',
    // Reserved and used are shown together because a finished job with credits reserved and none
    // used is the open settlement gap, and splitting them across columns hides the pair.
    cell: (j) => `${j.creditsUsed} / ${j.creditsReserved}`,
    numeric: true,
  },
  {
    header: 'Settled',
    cell: (j) =>
      j.settledAt ? (
        formatDateTime(j.settledAt)
      ) : (
        <span className="text-muted-foreground">never</span>
      ),
  },
  { header: 'Created', cell: (j) => formatDateTime(j.createdAt) },
];

function filterHref(status?: ResearchJobStatus): string {
  return status ? `/admin/jobs?status=${status}` : '/admin/jobs';
}

export default async function AdminJobsPage({
  searchParams,
}: {
  searchParams: Promise<{ cursor?: string | string[]; status?: string | string[] }>;
}) {
  const { cursor, status: rawStatus } = await searchParams;
  const status = asJobStatus(Array.isArray(rawStatus) ? rawStatus[0] : rawStatus);
  const page = await listJobs(cursor, status);

  const nextParams = new URLSearchParams();
  if (page.nextCursor) nextParams.set('cursor', page.nextCursor);
  if (status) nextParams.set('status', status);

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Research jobs</h1>
        <p className="text-sm text-muted-foreground">
          Every org, newest first. Open a job to see its tasks and what each one searched for.
        </p>
      </div>
      <nav aria-label="Filter by status" className="flex flex-wrap gap-2 text-sm">
        <Link
          href={filterHref()}
          aria-current={status ? undefined : 'page'}
          className={`rounded-md border px-2 py-1 ${status ? 'hover:bg-accent' : 'bg-accent'}`}
        >
          All
        </Link>
        {RESEARCH_JOB_STATUSES.map((s) => (
          <Link
            key={s}
            href={filterHref(s)}
            aria-current={status === s ? 'page' : undefined}
            className={`rounded-md border px-2 py-1 ${status === s ? 'bg-accent' : 'hover:bg-accent'}`}
          >
            {s}
          </Link>
        ))}
      </nav>
      <AdminTable
        caption="Research jobs"
        columns={columns}
        rows={page.items}
        nextHref={page.nextCursor ? `/admin/jobs?${nextParams.toString()}` : null}
        firstHref={cursor ? filterHref(status) : null}
      />
    </div>
  );
}
