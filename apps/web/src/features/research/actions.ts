'use server';

import type { ResearchSpec } from '@leadforge/contracts';
import { redirect } from 'next/navigation';
import { z } from 'zod';

import { ApiError, apiFetch } from '@/lib/api/server';

import { nonce } from './idempotency';
import type { ParseResult } from './types';

const PromptSchema = z.object({
  rawQuery: z.string().trim().min(1, 'Describe the leads you want.').max(2000),
});

export interface ParseState {
  result?: ParseResult;
  rawQuery?: string;
  error?: string;
  fieldErrors?: Record<string, string[]>;
}

/**
 * Turn a sentence into a reviewable spec. Nothing is charged for this (docs/06), which is why the
 * screen can afford to make the user look at the result before spending anything.
 */
export async function parseResearch(_prev: ParseState, form: FormData): Promise<ParseState> {
  const parsed = PromptSchema.safeParse({ rawQuery: form.get('rawQuery') });
  if (!parsed.success) {
    const flat = z.flattenError(parsed.error);
    return { fieldErrors: flat.fieldErrors };
  }
  const rawQuery = parsed.data.rawQuery;

  try {
    const result = await apiFetch<ParseResult>('/app/research/parse', {
      method: 'POST',
      body: JSON.stringify({ rawQuery }),
    });
    return { result, rawQuery };
  } catch (err) {
    return { rawQuery, error: messageFor(err, 'We could not read that just now.') };
  }
}

export interface RunState {
  error?: string;
  /** Set when the run was refused for money, so the UI can offer a top-up rather than an error. */
  needsCredits?: boolean;
}

/**
 * Start the run. A Server Action rather than a browser call, for two reasons: the same-origin
 * proxy does not forward `Idempotency-Key` (its request header allow-list is deliberately short),
 * and a double-submitted form must not start — or reserve credits for — two jobs.
 */
export async function runResearch(_prev: RunState, form: FormData): Promise<RunState> {
  const rawQuery = form.get('rawQuery');
  const specJson = form.get('spec');
  const idempotencyKey = form.get('idempotencyKey');
  if (typeof rawQuery !== 'string' || typeof specJson !== 'string') {
    return { error: 'Something went wrong reading that request. Try describing it again.' };
  }

  let spec: ResearchSpec;
  try {
    spec = JSON.parse(specJson) as ResearchSpec;
  } catch {
    return { error: 'Something went wrong reading that request. Try describing it again.' };
  }

  let jobId: string;
  try {
    const created = await apiFetch<{ jobId: string }>('/app/research', {
      method: 'POST',
      body: JSON.stringify({ rawQuery, spec }),
      headers:
        typeof idempotencyKey === 'string' && idempotencyKey
          ? { 'idempotency-key': idempotencyKey }
          : undefined,
    });
    jobId = created.jobId;
  } catch (err) {
    if (err instanceof ApiError && err.status === 402) {
      return {
        needsCredits: true,
        error: err.problem?.detail ?? 'Not enough credits for this run.',
      };
    }
    return { error: messageFor(err, 'We could not start that run just now.') };
  }
  // Outside the try: redirect throws by design, and catching it would turn a successful run into
  // an error message.
  redirect(`/research/${jobId}`);
}

export async function cancelResearch(form: FormData): Promise<void> {
  const id = form.get('jobId');
  if (typeof id !== 'string') return;
  try {
    await apiFetch(`/app/research/${id}/cancel`, { method: 'POST' });
  } catch {
    // The page re-reads the job either way, so a failed cancel shows as "still running" rather
    // than as a second error the user cannot act on.
  }
  redirect(`/research/${id}`);
}

/**
 * Runs an earlier search again, as it was asked the first time.
 *
 * Reads the stored spec rather than re-parsing the sentence: the customer may have removed a
 * chip or changed the depth before running it, and "run that again" means the thing that ran,
 * not the thing that was typed.
 *
 * A fresh idempotency key every time, on purpose. The key exists to swallow a double-click, and
 * this is the opposite: deliberately asking for the same leads again because the world has
 * moved on since. The API remembers a key for 24 hours, so reusing the original would return
 * the old job and look like nothing happened -- which is exactly the bug that cost a day.
 */
export async function rerunResearch(form: FormData): Promise<void> {
  const id = form.get('jobId');
  if (typeof id !== 'string') return;

  let jobId: string;
  try {
    const previous = await apiFetch<{ rawQuery: string; spec: ResearchSpec }>(
      `/app/research/${id}`,
    );
    const created = await apiFetch<{ jobId: string }>('/app/research', {
      method: 'POST',
      body: JSON.stringify({ rawQuery: previous.rawQuery, spec: previous.spec }),
      headers: { 'idempotency-key': `web:rerun:${id}:${nonce()}` },
    });
    jobId = created.jobId;
  } catch {
    // Back to the history, where the run that did not start is still visible. The alternative
    // is an error page that loses the list the person was working through.
    redirect('/research/history');
  }
  redirect(`/research/${jobId}`);
}

/** The API's own words where it has them; ours only as a fallback. */
function messageFor(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.status === 429) return 'That was a lot of requests. Give it a moment and try again.';
    if (err.status === 503) return 'The research workers are busy. Try again in a moment.';
    return err.problem?.detail ?? err.problem?.title ?? fallback;
  }
  return fallback;
}
