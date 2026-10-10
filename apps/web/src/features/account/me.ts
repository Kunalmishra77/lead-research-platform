import 'server-only';

import { cache } from 'react';

import { apiFetch } from '@/lib/api/server';

export interface Membership {
  orgId: string;
  orgName: string;
  orgSlug: string;
  workspaceId: string;
  workspaceName: string;
  role: string;
  /** The org's credit balance, carried here so the sidebar needs no call of its own. */
  creditsBalance: number;
}

export interface Me {
  user: { id: string; email: string | null; fullName: string | null; isPlatformStaff: boolean };
  memberships: Membership[];
}

/** GET /app/me, deduplicated per request. */
export const getMe = cache((): Promise<Me> => apiFetch<Me>('/app/me'));
