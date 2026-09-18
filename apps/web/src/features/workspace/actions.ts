'use server';

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';

import { env } from '@/env';
import { getMe } from '@/features/account/me';
import { WORKSPACE_COOKIE, workspaceCookieOptions } from '@/lib/http/workspace-cookie';

/**
 * Switches the active workspace. A server action (POST with Next's built-in Origin check), so other
 * sites cannot switch a user's workspace by linking to a URL. Only the user's own workspaces.
 */
export async function selectWorkspace(form: FormData): Promise<void> {
  const id = form.get('workspaceId');
  const me = await getMe();
  if (typeof id === 'string' && me.memberships.some((m) => m.workspaceId === id)) {
    (await cookies()).set(WORKSPACE_COOKIE, id, workspaceCookieOptions(env().APP_URL));
  }
  redirect('/dashboard');
}
