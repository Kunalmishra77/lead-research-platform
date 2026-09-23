/** SQLSTATE of a postgres.js error, walking `cause` because Drizzle wraps driver errors. */
export function pgCode(err: unknown): string | undefined {
  for (let e: unknown = err; e instanceof Error; e = e.cause) {
    const code = (e as { code?: unknown }).code;
    if (typeof code === 'string' && /^[0-9A-Z]{5}$/.test(code)) return code;
  }
  return undefined;
}

/** Postgres tells us to retry: deadlock detected / serialization failure. */
export const RETRYABLE_SQLSTATES = new Set(['40P01', '40001']);

export function isRetryable(err: unknown): boolean {
  const code = pgCode(err);
  return code !== undefined && RETRYABLE_SQLSTATES.has(code);
}
