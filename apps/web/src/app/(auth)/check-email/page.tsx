import { MailCheck } from 'lucide-react';
import type { Metadata } from 'next';
import Link from 'next/link';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

export const metadata: Metadata = { title: 'Check your email' };

export default async function CheckEmailPage({
  searchParams,
}: {
  searchParams: Promise<{ email?: string }>;
}) {
  const { email } = await searchParams;
  return (
    <Card>
      <CardHeader className="items-center text-center">
        <MailCheck aria-hidden className="size-10 text-primary" />
        <CardTitle>Confirm your email</CardTitle>
        <CardDescription>
          We sent a confirmation link{email ? ` to ${email}` : ''}. Open it to activate your
          account.
        </CardDescription>
      </CardHeader>
      <CardContent className="text-center text-sm">
        <Link className="font-medium text-primary hover:underline" href="/login">
          Back to sign in
        </Link>
      </CardContent>
    </Card>
  );
}
