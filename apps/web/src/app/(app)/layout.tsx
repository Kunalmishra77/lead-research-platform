import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import type { ReactNode } from 'react';

import { getMe } from '@/features/account/me';
import { AppShell } from '@/features/shell/app-shell';
import { WORKSPACE_COOKIE } from '@/lib/http/workspace-cookie';

/** Protected area: proxy.ts guarantees a session; this ensures an org and an active workspace. */
export default async function AppLayout({ children }: { children: ReactNode }) {
  const me = await getMe();
  if (me.memberships.length === 0) redirect('/onboarding');

  const selected = (await cookies()).get(WORKSPACE_COOKIE)?.value;
  const active = me.memberships.find((m) => m.workspaceId === selected);
  if (!active) redirect('/workspace/select?next=/dashboard');

  return (
    <AppShell user={me.user} memberships={me.memberships} activeWorkspaceId={active.workspaceId}>
      {children}
    </AppShell>
  );
}
