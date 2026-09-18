import type { Metadata } from 'next';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { signUp } from '@/features/auth/actions';
import { AuthForm } from '@/features/auth/auth-form';

export const metadata: Metadata = { title: 'Create account' };

export default function SignupPage() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Create your account</CardTitle>
        <CardDescription>
          We will email you a link to confirm your address before you can start.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <AuthForm mode="signup" action={signUp} />
      </CardContent>
    </Card>
  );
}
