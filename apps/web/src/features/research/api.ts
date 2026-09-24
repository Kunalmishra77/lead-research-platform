import 'server-only';

import { redirect } from 'next/navigation';
import { notFound } from 'next/navigation';

import { ApiError, apiFetch } from '@/lib/api/server';

import type { LeadsPage, ResearchJobView, ResearchPage } from './types';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const PAGE_SIZE = 50;

/** Only a well-formed cursor is forwarded; anything else starts from the first page. */
function query(cursor: unknown, limit = PAGE_SIZE): string {
  const params = new URLSearchParams({ limit: String(limit) });
  if (typeof cursor === 'string' && UUID.test(cursor)) params.set('cursor', cursor);
  return params.toString();
}

/**
 * A refusal from the API becomes the page the user should actually see.
 *
 * 404 covers both "no such job" and "a job in another workspace" — the API answers the same way
 * for both on purpose, so ids cannot be probed, and the UI must not undo that by distinguishing
 * them. 403 here means the role lacks `contacts.view`, which for a page is also a 404: there is
 * nothing at this address for this person.
 */
async function researchFetch<T>(path: string): Promise<T> {
  try {
    return await apiFetch<T>(path);
  } catch (err) {
    if (err instanceof ApiError && (err.status === 404 || err.status === 403)) notFound();
    if (err instanceof ApiError && err.status === 401) redirect('/login');
    throw err;
  }
}

export function listJobs(cursor: unknown): Promise<ResearchPage> {
  return researchFetch<ResearchPage>(`/app/research?${query(cursor)}`);
}

export function getJob(id: string): Promise<ResearchJobView> {
  return researchFetch<ResearchJobView>(`/app/research/${id}`);
}

export function getResults(id: string, cursor?: unknown): Promise<LeadsPage> {
  return researchFetch<LeadsPage>(`/app/research/${id}/results?${query(cursor)}`);
}
