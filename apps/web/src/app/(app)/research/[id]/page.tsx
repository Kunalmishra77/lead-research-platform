import type { Metadata } from 'next';
import Link from 'next/link';

import { Button } from '@/components/ui/button';
import { cancelResearch } from '@/features/research/actions';
import { getJob, getResults } from '@/features/research/api';
import { JobLive } from '@/features/research/components/job-live';
import { ResultsTable } from '@/features/research/components/results-table';
import { TERMINAL_STATUSES } from '@/features/research/types';

export const metadata: Metadata = { title: 'Research job' };

export default async function ResearchJobPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ cursor?: string }>;
}) {
  const { id } = await params;
  const { cursor } = await searchParams;
  // Both server-side: the page is useful the moment it renders, and the live stream then keeps
  // the counters current rather than being the only way to see anything.
  const [job, results] = await Promise.all([getJob(id), getResults(id, cursor)]);
  const running = !TERMINAL_STATUSES.includes(job.status);

  return (
    <div className="mx-auto w-full max-w-5xl space-y-8 p-6">
      <header className="space-y-2">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <Link
              className="text-muted-foreground hover:text-foreground text-xs underline underline-offset-2"
              href="/research/history"
            >
              ← All research
            </Link>
            <h1 className="text-xl font-medium">{job.rawQuery}</h1>
          </div>
          {running && (
            <form action={cancelResearch}>
              <input name="jobId" type="hidden" value={job.id} />
              <Button size="sm" type="submit" variant="outline">
                Stop this run
              </Button>
            </form>
          )}
        </div>
        <p className="text-muted-foreground text-xs">
          {job.depth} depth · started{' '}
          {new Date(job.createdAt).toLocaleString('en-IN', {
            dateStyle: 'medium',
            timeStyle: 'short',
          })}
        </p>
      </header>

      <JobLive initial={job} />

      <section aria-labelledby="results" className="space-y-3">
        <h2 className="text-base font-medium" id="results">
          Leads
        </h2>
        <ResultsTable jobId={job.id} page={results} />
      </section>
    </div>
  );
}
