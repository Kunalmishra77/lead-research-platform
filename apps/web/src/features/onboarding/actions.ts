'use server';

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { z } from 'zod';

import { env } from '@/env';
import { ApiError, apiFetch } from '@/lib/api/server';
import { WORKSPACE_COOKIE, workspaceCookieOptions } from '@/lib/http/workspace-cookie';

export interface OnboardingState {
  error?: string;
}

const schema = z.object({ name: z.string().trim().min(2, 'Enter your company name').max(120) });

interface CreatedOrg {
  org: { id: string; name: string; slug: string };
  workspace: { id: string; name: string };
}

export async function createOrganization(
  _prev: OnboardingState,
  form: FormData,
): Promise<OnboardingState> {
  const parsed = schema.safeParse({ name: form.get('name') });
  if (!parsed.success) return { error: parsed.error.issues[0]?.message ?? 'Invalid name' };
  let created: CreatedOrg;
  try {
    created = await apiFetch<CreatedOrg>('/app/orgs', {
      method: 'POST',
      body: JSON.stringify({ name: parsed.data.name }),
    });
  } catch (err) {
    if (err instanceof ApiError && err.problem) {
      return { error: err.problem.detail ?? err.problem.title };
    }
    return { error: 'Could not create the organization. Try again in a moment.' };
  }
  (await cookies()).set(
    WORKSPACE_COOKIE,
    created.workspace.id,
    workspaceCookieOptions(env().APP_URL),
  );
  redirect('/dashboard');
}
