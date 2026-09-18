import 'server-only';

import { cookies } from 'next/headers';

import { env } from '@/env';
import { WORKSPACE_COOKIE } from '@/lib/http/workspace-cookie';
import { createSupabaseServerClient } from '@/lib/supabase/server';

export { WORKSPACE_COOKIE };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** RFC 7807 body returned by the API. */
export interface Problem {
  type: string;
  title: string;
  status: number;
  code: string;
  detail?: string;
  request_id: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly problem: Problem | null,
  ) {
    super(problem?.title ?? `API request failed with ${String(status)}`);
    this.name = 'ApiError';
  }
}

/** Headers that authenticate a server-side call to the API as the current user. */
export async function apiAuthHeaders(): Promise<Record<string, string>> {
  const supabase = await createSupabaseServerClient();
  // getSession() reads the cookie without verifying it. That is fine here because the token is only
  // forwarded: the API verifies every token against the JWKS and checks the session is active.
  const { data } = await supabase.auth.getSession();
  const headers: Record<string, string> = {};
  if (data.session) headers.authorization = `Bearer ${data.session.access_token}`;
  const workspace = (await cookies()).get(WORKSPACE_COOKIE)?.value;
  if (workspace && UUID.test(workspace)) headers['x-workspace-id'] = workspace;
  return headers;
}

/** Server-side JSON call to the API (Server Components / Actions). */
export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  for (const [name, value] of Object.entries(await apiAuthHeaders())) headers.set(name, value);
  if (init.body !== undefined && !headers.has('content-type')) {
    headers.set('content-type', 'application/json');
  }
  const res = await fetch(new URL(path, env().API_URL), { ...init, headers, cache: 'no-store' });
  if (!res.ok) {
    const problem = res.headers.get('content-type')?.includes('json')
      ? ((await res.json()) as Problem)
      : null;
    throw new ApiError(res.status, problem);
  }
  return (await res.json()) as T;
}
