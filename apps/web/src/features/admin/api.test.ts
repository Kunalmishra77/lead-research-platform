// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.fn<(path: string) => Promise<unknown>>();

vi.mock('server-only', () => ({}));
vi.mock('next/navigation', () => ({
  notFound: vi.fn(() => {
    throw new Error('NOT_FOUND');
  }),
  redirect: vi.fn((to: string) => {
    throw new Error(`REDIRECT ${to}`);
  }),
}));
vi.mock('@/lib/api/server', () => {
  class ApiError extends Error {
    constructor(readonly status: number) {
      super(String(status));
    }
  }
  return { ApiError, apiFetch };
});

const { ApiError } = await import('@/lib/api/server');
const { listOrgs, listUsers } = await import('./api');

beforeEach(() => {
  apiFetch.mockReset();
});

describe('admin fetchers', () => {
  it('forwards only a well-formed cursor', async () => {
    apiFetch.mockResolvedValue({ items: [], nextCursor: null });
    await listUsers('0190a000-0000-7000-8000-000000000001');
    await listOrgs('../../x');
    await listOrgs(['a', 'b']);
    expect(apiFetch.mock.calls.map((c) => c[0])).toEqual([
      '/admin/users?limit=50&cursor=0190a000-0000-7000-8000-000000000001',
      '/admin/orgs?limit=50',
      '/admin/orgs?limit=50',
    ]);
  });

  it('turns 403 into a 404 and 401 into sign-in', async () => {
    apiFetch.mockRejectedValueOnce(new ApiError(403, null));
    await expect(listOrgs(undefined)).rejects.toThrow('NOT_FOUND');
    apiFetch.mockRejectedValueOnce(new ApiError(401, null));
    await expect(listUsers(undefined)).rejects.toThrow('REDIRECT /login');
    apiFetch.mockRejectedValueOnce(new ApiError(500, null));
    await expect(listUsers(undefined)).rejects.toThrow('500');
  });
});
