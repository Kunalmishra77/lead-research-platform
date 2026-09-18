import { type NextRequest, NextResponse } from 'next/server';

import { env } from '@/env';
import { getMe } from '@/features/account/me';
import { safeNextPath } from '@/lib/http/safe-next';
import { WORKSPACE_COOKIE, workspaceCookieOptions } from '@/lib/http/workspace-cookie';

/**
 * Picks the user's default workspace when none is active (the (app) layout redirects here, since
 * Server Components cannot set cookies). It never replaces a valid existing choice, so a cross-site
 * link cannot switch workspaces; explicit switching is the `selectWorkspace` server action.
 */
export async function GET(request: NextRequest): Promise<NextResponse> {
  const appUrl = env().APP_URL;
  const next = safeNextPath(request.nextUrl.searchParams.get('next') ?? undefined, appUrl);
  const me = await getMe();
  const current = request.cookies.get(WORKSPACE_COOKIE)?.value;
  const response = NextResponse.redirect(new URL(next, appUrl));
  if (me.memberships.some((m) => m.workspaceId === current)) return response;

  const first = me.memberships[0];
  if (!first) return NextResponse.redirect(new URL('/onboarding', appUrl));
  response.cookies.set(WORKSPACE_COOKIE, first.workspaceId, workspaceCookieOptions(appUrl));
  return response;
}
