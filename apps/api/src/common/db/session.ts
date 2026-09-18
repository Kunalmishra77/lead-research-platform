import { sql, type Transaction } from '@leadforge/db';

import { AppError } from '../errors/app-error';

/**
 * Access tokens stay valid until expiry, so every privileged path re-checks the Supabase session:
 * signed-out/revoked sessions and banned/deleted users are refused with 401.
 */
export async function assertSessionActive(tx: Transaction, sessionId: string): Promise<void> {
  const [row] = await tx.execute<{ active: boolean }>(
    sql`select app.session_is_active(${sessionId}::uuid) as active`,
  );
  if (row?.active !== true) throw sessionRevoked();
}

export function sessionRevoked(): AppError {
  return new AppError({
    code: 'auth.session_revoked',
    httpStatus: 401,
    title: 'Session is no longer valid',
    detail: 'Sign in again',
  });
}
