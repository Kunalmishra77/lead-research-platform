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
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 className="text-base font-medium" id="results">
            Leads
          </h2>
          {results.items.length > 0 && <ExportLinks jobId={job.id} />}
        </div>
        <ResultsTable finished={!running} jobId={job.id} page={results} />
      </section>
    </div>
  );
}

/**
 * Two downloads, because they answer different questions.
 *
 * The plain CSV is what goes into a dialler or a CRM. The one with sources adds a source URL and
 * an observed date beside every value (docs/14's "With sources" preset), which is what makes the
 * file checkable by whoever receives it rather than a list of assertions from a tool they have
 * never heard of.
 *
 * Plain links, not buttons with handlers: a download is a GET, and the browser already knows how
 * to do one. They go through this app's own `/api/app` proxy, so the access token stays
 * server-side (ADR-0002).
 */
function ExportLinks({ jobId }: { jobId: string }) {
  return (
    <span className="flex flex-wrap items-center gap-2 text-sm">
      <Button asChild size="sm" variant="outline">
        <a download href={`/api/app/research/${jobId}/export.csv`}>
          Download CSV
        </a>
      </Button>
      <Button asChild size="sm" variant="ghost">
        <a download href={`/api/app/research/${jobId}/export.csv?columns=sources`}>
          with sources
        </a>
      </Button>
    </span>
  );
}
