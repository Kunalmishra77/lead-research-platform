import type { Metadata } from 'next';
import Link from 'next/link';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { listJobs } from '@/features/research/api';
import { StatusBadge } from '@/features/research/components/job-live';
import { RerunButton } from '@/features/research/components/rerun-button';
import { historyHref } from '@/features/research/history-query';
import { FILTERABLE_STATUSES } from '@/features/research/types';

export const metadata: Metadata = { title: 'Research history' };

/** Why a run stopped, in words the person who started it can act on (docs/05 error classes). */
const WHY: Record<string, string> = {
  budget_exhausted: 'ran out of credits',
  rate_limited: 'a source throttled us',
  access_restricted: 'a source refused us',
  invalid_input: 'the request could not be understood',
  parse_failed: 'a source answered with something unreadable',
  transient: 'a temporary failure it could not recover from',
};

export default async function HistoryPage({
  searchParams,
}: {
  searchParams: Promise<{ cursor?: string; status?: string; q?: string }>;
}) {
  const { cursor, status, q } = await searchParams;
  const page = await listJobs({ cursor, status, q });
  const filtered = Boolean(status) || Boolean(q?.trim());

  return (
    <div className="mx-auto w-full max-w-5xl space-y-5 p-6">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-medium">Research history</h1>
        <Link className="text-sm underline underline-offset-2" href="/research/new">
          New research
        </Link>
      </header>

      {/* A GET form, so a filtered history is a URL: shareable, bookmarkable, and still there
          after a reload. A client-side filter would lose all three for no gain. */}
      <form action="/research/history" className="flex flex-wrap items-center gap-2">
        <Input
          aria-label="Search what you asked for"
          className="h-8 max-w-64"
          defaultValue={q ?? ''}
          name="q"
          placeholder="Search your searches…"
          type="search"
        />
        {status ? <input name="status" type="hidden" value={status} /> : null}
        <Button size="sm" type="submit" variant="outline">
          Search
        </Button>
        <span className="flex flex-wrap items-center gap-1">
          <FilterLink active={!status} label="All" q={q} />
          {FILTERABLE_STATUSES.map((s) => (
            <FilterLink active={status === s} key={s} label={s} q={q} status={s} />
          ))}
        </span>
      </form>

      {page.items.length === 0 ? (
        <div className="border-border space-y-3 rounded-lg border border-dashed p-8 text-center">
          <p className="text-muted-foreground text-sm">
            {filtered
              ? 'No runs match that. Clear the filters to see the rest.'
              : 'You have not run any research yet.'}
          </p>
          <Link
            className="text-sm underline underline-offset-2"
            href={filtered ? '/research/history' : '/research/new'}
          >
            {filtered ? 'Show everything' : 'Describe what you need'}
          </Link>
        </div>
      ) : (
        <>
          <div className="border-border overflow-hidden rounded-lg border">
            <Table>
              <caption className="sr-only">Research jobs, newest first</caption>
              <TableHeader>
                <TableRow>
                  <TableHead scope="col">What you asked for</TableHead>
                  <TableHead scope="col">Status</TableHead>
                  <TableHead className="text-right" scope="col">
                    Leads
                  </TableHead>
                  <TableHead className="text-right" scope="col">
                    Credits
                  </TableHead>
                  <TableHead scope="col">Started</TableHead>
                  <TableHead scope="col">
                    <span className="sr-only">Actions</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {page.items.map((job) => (
                  <TableRow key={job.id}>
                    <TableCell className="max-w-72 align-top">
                      <Link
                        className="block truncate font-medium hover:underline"
                        href={`/research/${job.id}`}
                      >
                        {job.rawQuery}
                      </Link>
                      <span className="text-muted-foreground text-xs">
                        {job.depth} depth
                        {job.errorClass ? ` · ${WHY[job.errorClass] ?? job.errorClass}` : ''}
                      </span>
                    </TableCell>
                    <TableCell className="align-top">
                      <StatusBadge status={job.status} />
                    </TableCell>
                    <TableCell className="align-top text-right tabular-nums">
                      {job.leads > 0 ? (
                        <>
                          {job.leads.toLocaleString('en-IN')}
                          <span className="text-muted-foreground block text-xs">
                            {job.newLeads.toLocaleString('en-IN')} new
                          </span>
                        </>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell className="align-top text-right tabular-nums">
                      {job.creditsUsed.toLocaleString('en-IN')}
                      <span className="text-muted-foreground">
                        {' / '}
                        {job.creditsReserved.toLocaleString('en-IN')}
                      </span>
                    </TableCell>
                    <TableCell className="text-muted-foreground align-top whitespace-nowrap text-xs">
                      {new Date(job.createdAt).toLocaleString('en-IN', {
                        dateStyle: 'medium',
                        timeStyle: 'short',
                      })}
                    </TableCell>
                    <TableCell className="align-top text-right">
                      <RerunButton jobId={job.id} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          {page.nextCursor && (
            <Link
              className="text-muted-foreground hover:text-foreground text-sm underline underline-offset-2"
              href={historyHref({ cursor: page.nextCursor, status, q })}
            >
              Next page
            </Link>
          )}
        </>
      )}
    </div>
  );
}

/**
 * One status chip. A link rather than a button, because this changes which page you are on.
 *
 * It drops the cursor deliberately: page three of "all runs" is not page three of "failed ones",
 * and carrying the cursor across a filter change lands on an empty page that looks like no
 * results.
 */
function FilterLink({
  label,
  status,
  q,
  active,
}: {
  label: string;
  status?: string;
  q?: string;
  active: boolean;
}) {
  return (
    <Link
      className={`rounded-full border px-2.5 py-1 text-xs capitalize ${
        active
          ? 'border-primary bg-primary/10 text-foreground'
          : 'border-border text-muted-foreground hover:border-foreground/30'
      }`}
      href={historyHref({ status, q })}
    >
      {label}
    </Link>
  );
}
