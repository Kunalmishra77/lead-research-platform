'use server';

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { z } from 'zod';

import { env } from '@/env';
import { safeNextPath } from '@/lib/http/safe-next';
import { WORKSPACE_COOKIE } from '@/lib/http/workspace-cookie';
import { createSupabaseServerClient } from '@/lib/supabase/server';

export interface AuthFormState {
  error?: string;
  fieldErrors?: Partial<Record<'email' | 'password', string>>;
}

const credentials = z.object({
  email: z.email('Enter a valid email address').max(254),
  password: z.string().min(10, 'Use at least 10 characters').max(128),
});

function parse(form: FormData) {
  return credentials.safeParse({ email: form.get('email'), password: form.get('password') });
}

function fieldErrors(error: z.ZodError): AuthFormState['fieldErrors'] {
  const out: AuthFormState['fieldErrors'] = {};
  for (const issue of error.issues) {
    const field = issue.path[0];
    if ((field === 'email' || field === 'password') && !out[field]) out[field] = issue.message;
  }
  return out;
}

export async function signIn(_prev: AuthFormState, form: FormData): Promise<AuthFormState> {
  const parsed = parse(form);
  if (!parsed.success) return { fieldErrors: fieldErrors(parsed.error) };
  const supabase = await createSupabaseServerClient();
  const { error } = await supabase.auth.signInWithPassword(parsed.data);
  if (error) {
    // Same message for unknown email / wrong password. An unconfirmed account gets its own hint:
    // it needs the correct password first, so it does not help enumeration.
    return error.code === 'email_not_confirmed'
      ? { error: 'Confirm your email address first. Check your inbox for the link.' }
      : { error: 'Email or password is incorrect.' };
  }
  redirect(safeNextPath(form.get('next'), env().APP_URL));
}

export async function signUp(_prev: AuthFormState, form: FormData): Promise<AuthFormState> {
  const parsed = parse(form);
  if (!parsed.success) return { fieldErrors: fieldErrors(parsed.error) };
  const supabase = await createSupabaseServerClient();
  const { error } = await supabase.auth.signUp({
    ...parsed.data,
    options: { emailRedirectTo: `${env().APP_URL}/auth/confirm` },
  });
  if (error) {
    return error.code === 'weak_password'
      ? { fieldErrors: { password: 'Choose a stronger password.' } }
      : { error: 'Could not create the account. Try again in a moment.' };
  }
  redirect(`/check-email?email=${encodeURIComponent(parsed.data.email)}`);
}

export async function signOut(): Promise<void> {
  const supabase = await createSupabaseServerClient();
  await supabase.auth.signOut();
  // The workspace choice belongs to this user; the next person on this browser must not inherit it.
  (await cookies()).delete(WORKSPACE_COOKIE);
  redirect('/login');
}
