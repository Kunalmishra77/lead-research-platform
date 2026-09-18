import type { Metadata } from 'next';
import Link from 'next/link';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

export const metadata: Metadata = { title: 'Dashboard' };

export default function DashboardPage() {
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          Research runs, new leads and signals will appear here.
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
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
            <CardDescription>No research is running.</CardDescription>
          </CardHeader>
          <CardContent className="text-2xl font-semibold tabular-nums">0</CardContent>
        </Card>
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
      </div>
    </div>
  );
}
