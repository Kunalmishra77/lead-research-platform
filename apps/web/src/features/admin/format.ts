/** Short, locale-stable date-time for admin tables (UTC so server and client agree). */
export function formatDateTime(value: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return `${date.toISOString().slice(0, 16).replace('T', ' ')} UTC`;
}

/**
 * Money is stored in integer micros (CLAUDE.md). Shown to four decimal places because a single
 * Places call is about $0.035 and rounding it to cents would print $0.04 or $0.00 depending on the
 * call — a page about what things cost should not round the unit of cost away.
 */
export function formatMicros(micros: number): string {
  if (micros === 0) return '$0';
  return `$${(micros / 1_000_000).toFixed(4)}`;
}

/** Durations in admin tables are coarse on purpose: nobody reading this needs milliseconds. */
export function formatDuration(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms)) return '—';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  if (seconds < 90) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds % 60)}s`;
}
