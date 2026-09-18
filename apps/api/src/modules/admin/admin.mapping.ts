import { z } from 'zod';

import { pgCode } from '../../common/db/pg-error';
import { AppError } from '../../common/errors/app-error';
import type { AdminOrg, AdminUser, Page } from './admin.dto';

// postgres.js returns timestamptz as Date and int8 as string; validate instead of trusting casts.
const timestamp = z.union([z.date(), z.string()]).transform((v) => new Date(v).toISOString());
const count = z.union([z.string(), z.number(), z.bigint()]).transform(Number);

const OrgRow = z.object({
  id: z.string(),
  name: z.string(),
  slug: z.string(),
  plan: z.string(),
  region: z.string(),
  created_at: timestamp,
  member_count: count,
});

const UserRow = z.object({
  id: z.string(),
  email: z.string().nullable(),
  created_at: timestamp.nullable(), // nullable in auth.users
  email_confirmed_at: timestamp.nullable(),
  last_sign_in_at: timestamp.nullable(),
  is_platform_staff: z.boolean(),
  org_count: count,
});

export function toAdminOrg(raw: unknown): AdminOrg {
  const r = OrgRow.parse(raw);
  return {
    id: r.id,
    name: r.name,
    slug: r.slug,
    plan: r.plan,
    region: r.region,
    createdAt: r.created_at,
    memberCount: r.member_count,
  };
}

export function toAdminUser(raw: unknown): AdminUser {
  const r = UserRow.parse(raw);
  return {
    id: r.id,
    email: r.email,
    createdAt: r.created_at,
    emailConfirmedAt: r.email_confirmed_at,
    lastSignInAt: r.last_sign_in_at,
    isPlatformStaff: r.is_platform_staff,
    orgCount: r.org_count,
  };
}

/**
 * Rows were fetched with `limit + 1`: the extra row only signals that another page exists, so an
 * exact multiple of the page size does not produce an empty last page.
 */
export function toPage<T extends { id: string }>(
  limit: number,
  rows: readonly unknown[],
  map: (raw: unknown) => T,
): Page<T> {
  const items = rows.slice(0, limit).map(map);
  const last = items.at(-1);
  return { items, nextCursor: rows.length > limit && last ? last.id : null };
}

/** insufficient_privilege from the admin functions means "not platform staff". */
export function mapAdminError(err: unknown): unknown {
  if (err instanceof AppError) return err;
  if (pgCode(err) === '42501') {
    return new AppError({
      code: 'admin.forbidden',
      httpStatus: 403,
      title: 'Platform staff only',
      errorClass: 'access_restricted',
      cause: err,
    });
  }
  return err;
}
