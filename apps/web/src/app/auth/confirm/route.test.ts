// @vitest-environment node
import { NextRequest } from 'next/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const verifyOtp = vi.fn();
const exchangeCodeForSession = vi.fn();

vi.mock('@/lib/supabase/server', () => ({
  createSupabaseServerClient: () =>
    Promise.resolve({ auth: { verifyOtp, exchangeCodeForSession } }),
}));

const { GET } = await import('./route');

const call = (query: string) => GET(new NextRequest(`https://app.test/auth/confirm${query}`));

beforeEach(() => {
  verifyOtp.mockReset().mockResolvedValue({ error: null });
  exchangeCodeForSession.mockReset().mockResolvedValue({ error: null });
});

describe('GET /auth/confirm', () => {
  it('verifies token_hash links and starts the session', async () => {
    const res = await call('?token_hash=h&type=email');
    expect(verifyOtp).toHaveBeenCalledWith({ token_hash: 'h', type: 'email' });
    expect(res.headers.get('location')).toBe('https://app.test/dashboard');
  });

  it('exchanges a PKCE code as a fallback', async () => {
    const res = await call('?code=c');
    expect(exchangeCodeForSession).toHaveBeenCalledWith('c');
    expect(res.headers.get('location')).toBe('https://app.test/dashboard');
  });

  it('rejects unknown types, missing params and failed verification', async () => {
    expect((await call('?token_hash=h&type=magic')).headers.get('location')).toContain(
      '/login?error=confirm',
    );
    expect((await call('')).headers.get('location')).toContain('/login?error=confirm');
    verifyOtp.mockResolvedValue({ error: new Error('expired') });
    expect((await call('?token_hash=h&type=email')).headers.get('location')).toContain(
      '/login?error=confirm',
    );
    expect(exchangeCodeForSession).not.toHaveBeenCalled();
  });
});
