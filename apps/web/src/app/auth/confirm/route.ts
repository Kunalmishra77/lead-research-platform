import type { EmailOtpType } from '@supabase/supabase-js';
import { type NextRequest, NextResponse } from 'next/server';

import { createSupabaseServerClient } from '@/lib/supabase/server';

const OTP_TYPES: readonly EmailOtpType[] = [
  'signup',
  'email',
  'recovery',
  'invite',
  'email_change',
];

function isOtpType(value: string | null): value is EmailOtpType {
  return OTP_TYPES.some((t) => t === value);
}

/**
 * Email link target; verifies server-side and starts the session (ADR-0002: the browser never
 * talks to supabase.co). Preferred: `?token_hash=&type=` from our email templates (infra/setup/
 * SETUP.md), so the link points at our own domain. Fallback: `?code=` (PKCE) when Supabase's
 * default template redirected here; the verifier cookie was set by the server-side sign-up.
 */
export async function GET(request: NextRequest): Promise<NextResponse> {
  const params = request.nextUrl.searchParams;
  const tokenHash = params.get('token_hash');
  const type = params.get('type');
  const code = params.get('code');
  if ((tokenHash && isOtpType(type)) || code) {
    const supabase = await createSupabaseServerClient();
    const { error } =
      tokenHash && isOtpType(type)
        ? await supabase.auth.verifyOtp({ token_hash: tokenHash, type })
        : await supabase.auth.exchangeCodeForSession(code ?? '');
    if (!error) return NextResponse.redirect(new URL('/dashboard', request.url));
  }
  return NextResponse.redirect(new URL('/login?error=confirm', request.url));
}
