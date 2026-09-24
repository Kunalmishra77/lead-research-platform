'use client';

import { useEffect, useRef, useState } from 'react';

import { Badge } from '@/components/ui/badge';

import type { JobProgress, JobStatus, ProgressMessage, ResearchJobView } from '../types';
import { TERMINAL_STATUSES } from '../types';

const STAGES = ['planning', 'discovery'] as const;

/**
 * The job as it happens.
 *
 * Seeded from the server render so the page is never blank, then kept current by the SSE stream
 * (`state` → `progress`* → `done`). The stream is the live view and the server render is the
 * durable one; when they disagree the durable one wins, because a browser that reconnected may
 * have missed events while it was away.
 */
export function JobLive({ initial }: { initial: ResearchJobView }) {
  const [job, setJob] = useState(initial);
  const [live, setLive] = useState(true);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (TERMINAL_STATUSES.includes(initial.status)) return;

    const source = new EventSource(`/api/app/research/${initial.id}/events`);
    sourceRef.current = source;

    source.addEventListener('state', (e) => {
      setJob(jobFrom(e));
    });
    source.addEventListener('progress', (e) => {
      const event = progressFrom(e);
      setJob((current) => ({
        ...current,
        // A running task reports the job's counters, not its own share.
        progress: { ...current.progress, ...countsOf(event) },
        credits: { ...current.credits, used: event.credits_used || current.credits.used },
      }));
    });
    source.addEventListener('done', (e) => {
      setJob(jobFrom(e));
      source.close();
      setLive(false);
    });
    source.onerror = () => {
      // EventSource reconnects on its own; a closed one will not, and the stream also ends
      // silently at the server's ten-minute deadline. Either way the job keeps running.
      if (source.readyState === EventSource.CLOSED) setLive(false);
    };

    // Five concurrent streams per user, so leaving this open on navigation locks the next page
    // out of its own progress.
    return () => {
      source.close();
      sourceRef.current = null;
    };
  }, [initial.id, initial.status]);

  const done = TERMINAL_STATUSES.includes(job.status);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <StatusBadge status={job.status} />
        {!done && live && (
          <span aria-live="polite" className="text-muted-foreground text-xs">
            Watching for updates…
          </span>
        )}
        {!done && !live && (
          <span className="text-muted-foreground text-xs" role="status">
            Lost the live connection. The job is still running — reload to catch up.
          </span>
        )}
      </div>

      <StageProgress status={job.status} />

      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Counter label="Businesses seen" value={job.progress?.candidates} />
        <Counter label="Leads delivered" value={job.progress?.leads} />
        <Counter label="Values stored" value={job.progress?.values} />
        <Counter label="Credits used" value={job.credits.used} />
      </dl>

      {job.progress?.message && (
        <p
          className={`rounded-lg border p-3 text-sm ${
            job.status === 'failed'
              ? 'border-danger/30 bg-danger/5 text-danger'
              : 'border-border text-muted-foreground'
          }`}
          role={job.status === 'failed' ? 'alert' : 'status'}
        >
          {job.progress.message}
        </p>
      )}
    </div>
  );
}

/**
 * The two payload shapes this stream sends.
 *
 * `MessageEvent.data` is `any`, and our own API is the only thing that writes the stream, so
 * these two functions are where that trust sits — named and concrete rather than one generic
 * helper, which would only be an assertion wearing a type parameter. If the stream ever needs
 * validating for real, it is these two bodies and nothing else.
 */
function jobFrom(event: MessageEvent): ResearchJobView {
  return JSON.parse(String(event.data)) as ResearchJobView;
}

function progressFrom(event: MessageEvent): ProgressMessage {
  return JSON.parse(String(event.data)) as ProgressMessage;
}

function countsOf(event: ProgressMessage): JobProgress {
  const counts = event.counts;
  const next: JobProgress = {};
  if (typeof counts.candidates === 'number') next.candidates = counts.candidates;
  if (typeof counts.leads === 'number') next.leads = counts.leads;
  if (typeof counts.values === 'number') next.values = counts.values;
  if (event.message) next.message = event.message;
  return next;
}

function Counter({ label, value }: { label: string; value: number | undefined }) {
  return (
    <div className="border-border rounded-lg border p-3">
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className="text-xl font-medium tabular-nums">{(value ?? 0).toLocaleString('en-IN')}</dd>
    </div>
  );
}

export function StatusBadge({ status }: { status: JobStatus }) {
  const variant =
    status === 'completed'
      ? 'success'
      : status === 'failed'
        ? 'danger'
        : status === 'cancelled' || status === 'paused'
          ? 'warning'
          : 'info';
  return <Badge variant={variant}>{status}</Badge>;
}

/**
 * Which stage the job is in. Only the two stages Phase 2 actually runs are shown — listing
 * crawling and scoring now would promise work that does not happen yet.
 */
function StageProgress({ status }: { status: JobStatus }) {
  const reached = (stage: (typeof STAGES)[number]) => {
    if (stage === 'planning') return status !== 'queued';
    return ['running', 'completed'].includes(status);
  };
  return (
    <ol className="flex items-center gap-2 text-xs">
      {STAGES.map((stage) => (
        <li
          className={`rounded-md border px-2 py-1 ${
            reached(stage)
              ? 'border-primary/40 bg-primary/5'
              : 'border-border text-muted-foreground'
          }`}
          key={stage}
        >
          {stage}
        </li>
      ))}
    </ol>
  );
}
