import type { Metadata } from 'next';
import { redirect } from 'next/navigation';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { getMe } from '@/features/account/me';
import { OrgForm } from '@/features/onboarding/org-form';

export const metadata: Metadata = { title: 'Set up your workspace' };

export default async function OnboardingPage() {
  const me = await getMe();
  if (me.memberships.length > 0) redirect('/dashboard');
  return (
    <main className="flex min-h-dvh items-center justify-center bg-muted/40 px-4 py-12">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Set up your workspace</CardTitle>
          <CardDescription>
            Your organization holds your team, credits and research. You can invite colleagues
            later.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <OrgForm />
        </CardContent>
      </Card>
    </main>
  );
}
