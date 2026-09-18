import { describe, expect, it } from 'vitest';

import { AppError } from '../../common/errors/app-error';
import { mapAdminError, toAdminOrg, toAdminUser, toPage } from './admin.mapping';

const org = (id: string) => ({
  id,
  name: 'Acme',
  slug: 'acme',
  plan: 'free',
  region: 'in',
  created_at: new Date('2026-09-18T09:00:00Z'),
  member_count: '3',
});

describe('toPage', () => {
  it('returns a cursor only when the extra row shows another page exists', () => {
    const full = toPage(2, [org('a'), org('b'), org('c')], toAdminOrg);
    expect(full.items.map((o) => o.id)).toEqual(['a', 'b']);
    expect(full.nextCursor).toBe('b');

    const exact = toPage(2, [org('a'), org('b')], toAdminOrg);
    expect(exact.nextCursor).toBeNull();

    expect(toPage(2, [], toAdminOrg)).toEqual({ items: [], nextCursor: null });
  });
});

describe('row mapping', () => {
  it('normalises driver types', () => {
    expect(toAdminOrg(org('a'))).toMatchObject({
      createdAt: '2026-09-18T09:00:00.000Z',
      memberCount: 3,
    });
    expect(
      toAdminUser({
        id: 'u',
        email: null,
        created_at: null,
        email_confirmed_at: '2026-09-18T09:00:00+00:00',
        last_sign_in_at: null,
        is_platform_staff: false,
        org_count: 0,
      }),
    ).toEqual({
      id: 'u',
      email: null,
      createdAt: null,
      emailConfirmedAt: '2026-09-18T09:00:00.000Z',
      lastSignInAt: null,
      isPlatformStaff: false,
      orgCount: 0,
    });
  });

  it('rejects unexpected row shapes', () => {
    expect(() => toAdminOrg({ id: 1 })).toThrow();
  });
});

describe('mapAdminError', () => {
  it('maps insufficient_privilege anywhere in the cause chain to admin.forbidden', () => {
    const driver = Object.assign(new Error('platform staff only'), { code: '42501' });
    const mapped = mapAdminError(new Error('query failed', { cause: driver }));
    expect(mapped).toBeInstanceOf(AppError);
    expect(mapped).toMatchObject({
      code: 'admin.forbidden',
      httpStatus: 403,
      errorClass: 'access_restricted',
    });
  });

  it('passes other errors through unchanged', () => {
    const other = Object.assign(new Error('boom'), { code: '08006' });
    expect(mapAdminError(other)).toBe(other);
    const app = new AppError({ code: 'x', httpStatus: 401, title: 'x' });
    expect(mapAdminError(app)).toBe(app);
  });
});
