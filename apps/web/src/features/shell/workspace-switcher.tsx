'use client';

import type { Membership } from '@/features/account/me';
import { selectWorkspace } from '@/features/workspace/actions';

/** Switches the active workspace via a server action (cookie is set server-side). */
export function WorkspaceSwitcher({
  memberships,
  activeWorkspaceId,
}: {
  memberships: Membership[];
  activeWorkspaceId: string;
}) {
  return (
    <form action={selectWorkspace}>
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Workspace
        <select
          name="workspaceId"
          className="h-9 rounded-md border bg-background px-2 text-sm text-foreground"
          defaultValue={activeWorkspaceId}
          onChange={(event) => event.currentTarget.form?.requestSubmit()}
        >
          {memberships.map((m) => (
            <option key={m.workspaceId} value={m.workspaceId}>
              {m.orgName} · {m.workspaceName}
            </option>
          ))}
        </select>
      </label>
    </form>
  );
}
