import type { Metadata } from 'next';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { signIn } from '@/features/auth/actions';
import { AuthForm } from '@/features/auth/auth-form';

export const metadata: Metadata = { title: 'Sign in' };

const MESSAGES: Record<string, string> = {
  confirm: 'That confirmation link is invalid or has expired. Sign in to request a new one.',
  config: 'Sign-in is not configured on this server (see infra/setup/SETUP.md).',
};

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  const { next, error } = await searchParams;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Sign in</CardTitle>
        <CardDescription>Welcome back. Pick up your research where you left it.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {error && MESSAGES[error] ? (
          <p role="alert" className="rounded-md bg-warning/10 px-3 py-2 text-sm">
            {MESSAGES[error]}
          </p>
        ) : null}
        <AuthForm mode="login" action={signIn} next={next} />
      </CardContent>
    </Card>
  );
}
