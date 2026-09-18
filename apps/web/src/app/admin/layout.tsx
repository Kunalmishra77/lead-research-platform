import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import type { ReactNode } from 'react';

import { getMe } from '@/features/account/me';

export const metadata: Metadata = { title: { template: '%s · Admin', default: 'Admin' } };

/**
 * Platform staff only. Non-staff get a plain 404 (the area is not advertised); the API enforces the
 * staff flag again on every call, so this check only shapes the UI.
 */
export default async function AdminLayout({ children }: { children: ReactNode }) {
  const me = await getMe();
  if (!me.user.isPlatformStaff) notFound();
  return (
    <div className="min-h-dvh">
      <header className="flex h-14 items-center justify-between border-b px-4 md:px-6">
        <div className="flex items-center gap-6">
          <span className="font-semibold tracking-tight">
            Lead<span className="text-primary">Forge</span> Admin
          </span>
          <nav aria-label="Admin" className="flex gap-4 text-sm">
            <Link href="/admin/orgs" className="hover:underline">
              Organizations
            </Link>
            <Link href="/admin/users" className="hover:underline">
              Users
            </Link>
          </nav>
        </div>
        <Link href="/dashboard" className="text-sm text-muted-foreground hover:underline">
          Back to app
        </Link>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6 md:px-6">{children}</main>
    </div>
  );
}
