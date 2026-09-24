import type { Metadata } from 'next';
import Link from 'next/link';

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

export const metadata: Metadata = { title: 'Research history' };

export default async function HistoryPage({
  searchParams,
}: {
  searchParams: Promise<{ cursor?: string }>;
}) {
  const { cursor } = await searchParams;
  const page = await listJobs(cursor);

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 p-6">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-medium">Research history</h1>
        <Link className="text-sm underline underline-offset-2" href="/research/new">
          New research
        </Link>
      </header>

      {page.items.length === 0 ? (
        <div className="border-border space-y-3 rounded-lg border border-dashed p-8 text-center">
          <p className="text-muted-foreground text-sm">You have not run any research yet.</p>
          <Link className="text-sm underline underline-offset-2" href="/research/new">
            Describe what you need
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
                  <TableHead scope="col">Depth</TableHead>
                  <TableHead scope="col">Status</TableHead>
                  <TableHead className="text-right" scope="col">
                    Credits used
                  </TableHead>
                  <TableHead scope="col">Started</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {page.items.map((job) => (
                  <TableRow key={job.id}>
                    <TableCell className="max-w-80 truncate font-medium">
                      <Link className="hover:underline" href={`/research/${job.id}`}>
                        {job.rawQuery}
                      </Link>
                    </TableCell>
                    <TableCell>{job.depth}</TableCell>
                    <TableCell>
                      <StatusBadge status={job.status} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {job.creditsUsed.toLocaleString('en-IN')}
                      <span className="text-muted-foreground">
                        {' / '}
                        {job.creditsReserved.toLocaleString('en-IN')}
                      </span>
                    </TableCell>
                    <TableCell className="text-muted-foreground whitespace-nowrap">
                      {new Date(job.createdAt).toLocaleString('en-IN', {
                        dateStyle: 'medium',
                        timeStyle: 'short',
                      })}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          {page.nextCursor && (
            <Link
              className="text-muted-foreground hover:text-foreground text-sm underline underline-offset-2"
              href={`/research/history?cursor=${page.nextCursor}`}
            >
              Next page
            </Link>
          )}
        </>
      )}
    </div>
  );
}
