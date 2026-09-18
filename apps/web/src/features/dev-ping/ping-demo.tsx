'use client';

import { CheckCircle2, CircleDashed, Loader2, XCircle } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input, Label } from '@/components/ui/input';

interface ProgressEvent {
  status: string;
  counts?: { step?: number; steps?: number; attempt?: number };
  message?: string | null;
  error_class?: string | null;
}

interface JobState {
  status: string;
  attempts: number;
  errorClass: string | null;
}

type Phase = 'idle' | 'starting' | 'streaming' | 'done' | 'error';

/** Starts a system.ping job and follows its SSE progress (same-origin proxy -> API -> Redis). */
export function PingDemo() {
  const [phase, setPhase] = useState<Phase>('idle');
  const [failTimes, setFailTimes] = useState(0);
  const [jobId, setJobId] = useState<string | null>(null);
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [final, setFinal] = useState<JobState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => () => sourceRef.current?.close(), []);

  async function start() {
    sourceRef.current?.close();
    setPhase('starting');
    setEvents([]);
    setFinal(null);
    setError(null);
    const res = await fetch('/api/app/dev/ping-job', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        message: 'hello from the web app',
        steps: 5,
        delayMs: 3000,
        failTimes,
      }),
    });
    if (!res.ok) {
      const problem = (await res.json().catch(() => null)) as { title?: string } | null;
      setError(problem?.title ?? `Request failed (${String(res.status)})`);
      setPhase('error');
      return;
    }
    const { jobId: id } = (await res.json()) as { jobId: string };
    setJobId(id);
    setPhase('streaming');

    const source = new EventSource(`/api/app/dev/ping-job/${id}/events`);
    sourceRef.current = source;
    source.addEventListener('progress', (event) => {
      setEvents((prev) => [
        ...prev,
        JSON.parse((event as MessageEvent<string>).data) as ProgressEvent,
      ]);
    });
    source.addEventListener('done', (event) => {
      setFinal(JSON.parse((event as MessageEvent<string>).data) as JobState);
      setPhase('done');
      source.close();
    });
    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED) return;
      setError('Lost the live connection; the job keeps running.');
    };
  }

  const last = events.at(-1);
  const steps = last?.counts?.steps ?? 5;
  const step = last?.counts?.step ?? 0;
  const percent = final?.status === 'completed' ? 100 : Math.round((step / steps) * 100);

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <CardTitle>Job pipeline check</CardTitle>
        <CardDescription>
          API → Redis Streams → Python worker → Postgres → progress over SSE. Set failures to 5 to
          watch retries end in the dead-letter queue.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <div className="flex items-end gap-3">
          <div className="flex flex-col gap-2">
            <Label htmlFor="fail-times">Simulated failures</Label>
            <Input
              id="fail-times"
              type="number"
              min={0}
              max={5}
              value={failTimes}
              className="w-24"
              onChange={(e) => {
                setFailTimes(Math.min(5, Math.max(0, Number(e.target.value))));
              }}
            />
          </div>
          <Button
            onClick={() => void start()}
            disabled={phase === 'starting' || phase === 'streaming'}
          >
            {phase === 'streaming' ? <Loader2 aria-hidden className="animate-spin" /> : null}
            Run ping job
          </Button>
        </div>

        {jobId ? <p className="font-mono text-xs text-muted-foreground">job {jobId}</p> : null}

        {phase !== 'idle' && phase !== 'error' ? (
          <div className="flex flex-col gap-2" aria-live="polite">
            <div
              className="h-2 overflow-hidden rounded-full bg-muted"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={percent}
            >
              <div
                className="h-full bg-primary transition-all"
                style={{ width: `${String(percent)}%` }}
              />
            </div>
            <ul className="flex flex-col gap-1 text-sm">
              {events.map((e, i) => (
                <li key={i} className="flex items-center gap-2">
                  {e.status === 'completed' ? (
                    <CheckCircle2 aria-hidden className="size-4 text-success" />
                  ) : e.status === 'failed' ? (
                    <XCircle aria-hidden className="size-4 text-danger" />
                  ) : (
                    <CircleDashed aria-hidden className="size-4 text-muted-foreground" />
                  )}
                  <span>
                    {e.status} · step {e.counts?.step ?? 0}/{e.counts?.steps ?? '?'} · attempt{' '}
                    {e.counts?.attempt ?? '?'}
                    {e.message ? ` · ${e.message}` : ''}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {final ? (
          <p
            role="status"
            className={
              final.status === 'completed'
                ? 'rounded-md bg-success/10 px-3 py-2 text-sm'
                : 'rounded-md bg-danger/10 px-3 py-2 text-sm'
            }
          >
            Finished: <strong>{final.status}</strong> after {final.attempts} attempt(s)
            {final.errorClass ? ` (${final.errorClass})` : ''}.
          </p>
        ) : null}
        {error ? (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
