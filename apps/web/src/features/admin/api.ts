import 'server-only';

import { notFound, redirect } from 'next/navigation';

import { ApiError, apiFetch } from '@/lib/api/server';

export interface Page<T> {
  items: T[];
  nextCursor: string | null;
}

export interface AdminOrg {
  id: string;
  name: string;
  slug: string;
  plan: string;
  region: string;
  createdAt: string;
  memberCount: number;
}

export interface AdminUser {
  id: string;
  email: string | null;
  createdAt: string | null;
  emailConfirmedAt: string | null;
  lastSignInAt: string | null;
  isPlatformStaff: boolean;
  orgCount: number;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const ADMIN_PAGE_SIZE = 50;

/** Only a well-formed cursor is forwarded; anything else starts from the first page. */
function query(cursor: unknown): string {
  const params = new URLSearchParams({ limit: String(ADMIN_PAGE_SIZE) });
  if (typeof cursor === 'string' && UUID.test(cursor)) params.set('cursor', cursor);
  return params.toString();
}

/**
 * Pages render in parallel with the layout's staff check, so they must not surface API refusals
 * as error pages: 403 (not staff) becomes the same 404 the layout gives, 401 goes to sign-in.
 */
async function adminFetch<T>(path: string): Promise<T> {
  try {
    return await apiFetch<T>(path);
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) notFound();
    if (err instanceof ApiError && err.status === 401) redirect('/login');
    throw err;
  }
}

/** Staff-only, audited on every call (app.admin_list_orgs). */
export function listOrgs(cursor: unknown): Promise<Page<AdminOrg>> {
  return adminFetch<Page<AdminOrg>>(`/admin/orgs?${query(cursor)}`);
}

/** Staff-only, audited on every call (app.admin_list_users). */
export function listUsers(cursor: unknown): Promise<Page<AdminUser>> {
  return adminFetch<Page<AdminUser>>(`/admin/users?${query(cursor)}`);
}
