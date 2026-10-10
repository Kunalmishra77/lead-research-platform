import type { Metadata } from 'next';
import Link from 'next/link';

import { Button } from '@/components/ui/button';
import { getDashboard } from '@/features/dashboard/api';
import { Overview } from '@/features/dashboard/components/overview';

export const metadata: Metadata = { title: 'Dashboard' };

export default async function DashboardPage() {
  const data = await getDashboard();
  // /dev/ping is a development-only route: it calls notFound() in production. The sidebar already
  // hides it there, and this page has to make the same choice or it offers a button that 404s.
  const showDeveloper = process.env.NODE_ENV !== 'production';

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-xl font-medium">Dashboard</h1>
          <p className="text-muted-foreground text-sm">
            Everything below is counted from what has actually been stored.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {showDeveloper ? (
            <Button asChild size="sm" variant="ghost">
              <Link href="/dev/ping">Ping job</Link>
            </Button>
          ) : null}
          <Button asChild size="sm">
            <Link href="/research/new">New research</Link>
          </Button>
        </div>
      </header>

      <Overview data={data} />
    </div>
  );
}
