import type { Metadata } from 'next';
import { cookies } from 'next/headers';

import { getMe } from '@/features/account/me';
import { NewResearch } from '@/features/research/components/new-research';
import { WORKSPACE_COOKIE } from '@/lib/http/workspace-cookie';

export const metadata: Metadata = { title: 'New research' };

/** Starting a run needs `research.run`; a viewer may look but not spend (docs/05 RBAC). */
const CAN_RUN = new Set(['owner', 'admin', 'manager', 'member']);

export default async function NewResearchPage() {
  const me = await getMe();
  const active = (await cookies()).get(WORKSPACE_COOKIE)?.value;
  const role = me.memberships.find((m) => m.workspaceId === active)?.role;

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 p-6">
      <header className="space-y-1">
        <h1 className="text-xl font-medium">New research</h1>
        <p className="text-muted-foreground text-sm">
          Describe the businesses you want. We read it back to you before anything is spent.
        </p>
      </header>
      <NewResearch canRun={Boolean(role && CAN_RUN.has(role))} />
    </div>
  );
}
