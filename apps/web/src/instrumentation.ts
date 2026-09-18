/** Runs once per server process (Next.js instrumentation hook). */
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    // Dev-only DoH for *.supabase.co (ADR-0002); a no-op unless DEV_DNS_OVER_HTTPS=true.
    const { installDevDnsFromEnv } = await import('@leadforge/dev-dns');
    installDevDnsFromEnv(process.env);
  }
}
