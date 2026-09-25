import type { Metadata } from 'next';
import Link from 'next/link';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

export const metadata: Metadata = { title: 'Dashboard' };

export default function DashboardPage() {
  // /dev/ping is a development-only route: it calls notFound() in production. The sidebar already
  // hides it there, and this card has to make the same choice or it offers a button that 404s.
  const showDeveloper = process.env.NODE_ENV !== 'production';
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          Research runs, new leads and signals will appear here.
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Credits</CardTitle>
            <CardDescription>Balance and usage arrive with billing.</CardDescription>
          </CardHeader>
          <CardContent className="text-2xl font-semibold tabular-nums">—</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Running jobs</CardTitle>
            <CardDescription>Live counts arrive with the dashboard feed.</CardDescription>
          </CardHeader>
          <CardContent className="text-2xl font-semibold tabular-nums">—</CardContent>
        </Card>
        {showDeveloper ? (
          <Card>
            <CardHeader>
              <CardTitle>Pipeline check</CardTitle>
              <CardDescription>Verify the job pipeline end to end.</CardDescription>
            </CardHeader>
            <CardContent>
              <Button asChild variant="outline" size="sm">
                <Link href="/dev/ping">Run a ping job</Link>
              </Button>
            </CardContent>
          </Card>
        ) : null}
      </div>
    </div>
  );
}
