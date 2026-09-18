// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from 'vitest';

const cookieStore = { set: vi.fn(), delete: vi.fn() };
const signOutMock = vi.fn(() => Promise.resolve({ error: null }));

vi.mock('next/headers', () => ({ cookies: () => Promise.resolve(cookieStore) }));
vi.mock('next/navigation', () => ({
  redirect: vi.fn((to: string) => {
    throw new Error(`REDIRECT ${to}`);
  }),
}));
vi.mock('@/env', () => ({
  env: () => ({ APP_URL: 'https://app.example.com', API_URL: 'http://api:4000' }),
}));
vi.mock('@/features/account/me', () => ({
  getMe: () =>
    Promise.resolve({
      memberships: [{ workspaceId: '0190a000-0000-7000-8000-000000000001' }],
    }),
}));
vi.mock('@/lib/supabase/server', () => ({
  createSupabaseServerClient: () => Promise.resolve({ auth: { signOut: signOutMock } }),
}));

const { selectWorkspace } = await import('./actions');
const { signOut } = await import('@/features/auth/actions');

function form(workspaceId: string): FormData {
  const data = new FormData();
  data.set('workspaceId', workspaceId);
  return data;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('selectWorkspace', () => {
  it('sets the cookie for a workspace the user belongs to', async () => {
    await expect(selectWorkspace(form('0190a000-0000-7000-8000-000000000001'))).rejects.toThrow(
      'REDIRECT /dashboard',
    );
    expect(cookieStore.set).toHaveBeenCalledWith(
      'lf_ws',
      '0190a000-0000-7000-8000-000000000001',
      expect.objectContaining({ httpOnly: true, secure: true }),
    );
  });

  it('ignores a workspace the user does not belong to', async () => {
    await expect(selectWorkspace(form('0190a000-0000-7000-8000-00000000beef'))).rejects.toThrow(
      'REDIRECT /dashboard',
    );
    expect(cookieStore.set).not.toHaveBeenCalled();
  });
});

describe('signOut', () => {
  it('signs out and clears the active workspace cookie', async () => {
    await expect(signOut()).rejects.toThrow('REDIRECT /login');
    expect(signOutMock).toHaveBeenCalled();
    expect(cookieStore.delete).toHaveBeenCalledWith('lf_ws');
  });
});
