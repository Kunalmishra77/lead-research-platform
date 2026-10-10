import { LogOut, Search, Shield } from 'lucide-react';
import Link from 'next/link';
import type { ReactNode } from 'react';

import { Button } from '@/components/ui/button';
import type { Me, Membership } from '@/features/account/me';
import { signOut } from '@/features/auth/actions';

import { SidebarNav } from './sidebar';
import { WorkspaceSwitcher } from './workspace-switcher';

/**
 * The org's credit balance, or an em-dash when there is no membership to read it from.
 *
 * It read `—` on every page for months with the comment "a placeholder until billing". The
 * number existed the whole time -- `organizations.credits_balance` is what every reservation
 * already debits -- so the placeholder was not waiting on billing, it was waiting on someone
 * passing it in.
 */
export function CreditsPill({ credits }: { credits: number | null }) {
  return (
    <span
      className="inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium"
      title="Credit balance"
    >
      <span aria-hidden className="size-2 rounded-full bg-primary" />
      {credits === null ? '— credits' : `${credits.toLocaleString('en-IN')} credits`}
    </span>
  );
}

interface AppShellProps {
  user: Me['user'];
  memberships: Membership[];
  activeWorkspaceId: string;
  children: ReactNode;
}

export function AppShell({ user, memberships, activeWorkspaceId, children }: AppShellProps) {
  return (
    <div className="flex min-h-dvh">
      <aside className="hidden w-60 shrink-0 flex-col justify-between border-r bg-sidebar p-4 md:flex">
        <div className="flex flex-col gap-6">
          <Link href="/dashboard" className="px-3 text-lg font-semibold tracking-tight">
            Lead<span className="text-primary">Forge</span>
          </Link>
          {/* /dev/* pages 404 in production (DevOnlyGuard on the API, notFound() on the page). */}
          <SidebarNav showDeveloper={process.env.NODE_ENV !== 'production'} />
        </div>
        <div className="flex flex-col gap-3">
          <CreditsPill
            credits={
              memberships.find((m) => m.workspaceId === activeWorkspaceId)?.creditsBalance ?? null
            }
          />
          <WorkspaceSwitcher memberships={memberships} activeWorkspaceId={activeWorkspaceId} />
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between gap-4 border-b px-4 md:px-6">
          {/* Command palette arrives in a later phase; until then this is a visual placeholder. */}
          <div
            aria-disabled="true"
            className="flex h-9 w-full max-w-md cursor-not-allowed items-center gap-2 rounded-md border bg-muted/40 px-3 text-sm text-muted-foreground"
          >
            <Search aria-hidden className="size-4" />
            <span>Search leads and companies (Ctrl+K)</span>
          </div>
          <div className="flex items-center gap-3">
            {user.isPlatformStaff ? (
              <Button asChild variant="ghost" size="sm">
                <Link href="/admin">
                  <Shield aria-hidden />
                  Admin
                </Link>
              </Button>
            ) : null}
            <span className="hidden text-sm text-muted-foreground lg:inline">{user.email}</span>
            <form action={signOut}>
              <Button
                type="submit"
                variant="ghost"
                size="icon"
                aria-label="Sign out"
                title="Sign out"
              >
                <LogOut aria-hidden />
              </Button>
            </form>
          </div>
        </header>
        <main className="flex-1 px-4 py-6 md:px-6">{children}</main>
      </div>
    </div>
  );
}
