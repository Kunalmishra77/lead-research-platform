import { z } from 'zod';

const bool = z.enum(['true', 'false']).transform((v) => v === 'true');

/** Every environment variable the API reads. Validated once at startup. */
export const envSchema = z
  .object({
    // Required on purpose: a missing value must not silently enable dev behaviour (Swagger, pretty logs).
    NODE_ENV: z.enum(['development', 'test', 'production']),
    HOST: z.string().default('0.0.0.0'),
    PORT: z.coerce.number().int().min(1).max(65535).default(4000),
    LOG_LEVEL: z
      .enum(['fatal', 'error', 'warn', 'info', 'debug', 'trace', 'silent'])
      .default('info'),
    APP_URL: z.url(),
    DEV_DNS_OVER_HTTPS: bool.default(false),

    DATABASE_URL: z.url(),
    DB_POOL_MAX: z.coerce.number().int().min(1).max(50).default(10),
    // Reverse proxies in front of the API: a hop count ("1") or comma-separated CIDRs. Unset = none.
    TRUST_PROXY: z
      .string()
      .optional()
      .refine((v) => v?.trim().toLowerCase() !== 'true', {
        message: 'use a hop count or proxy CIDRs, not "true" (lets clients spoof their IP)',
      })
      .transform((v): number | string[] | false => {
        if (v === undefined || v.trim() === '' || v === 'false') return false;
        if (/^\d+$/.test(v)) return Number(v);
        return v
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean);
      }),
    REDIS_URL: z.url(),

    S3_ENDPOINT: z.url(),
    S3_REGION: z.string().min(1),
    S3_BUCKET_RAW: z.string().min(3),
    S3_BUCKET_EXPORTS: z.string().min(3),
    S3_ACCESS_KEY_ID: z.string().min(1),
    S3_SECRET_ACCESS_KEY: z.string().min(1),

    // Normalized without trailing slash: the token issuer is `${SUPABASE_URL}/auth/v1`, exactly.
    SUPABASE_URL: z.url().transform((u) => u.replace(/\/+$/, '')),
    SUPABASE_JWKS_URL: z.url(),
  })
  .refine((c) => new URL(c.SUPABASE_JWKS_URL).origin === new URL(c.SUPABASE_URL).origin, {
    path: ['SUPABASE_JWKS_URL'],
    message: 'must be on the same origin as SUPABASE_URL',
  });

export type AppConfig = z.infer<typeof envSchema>;

/** Parses `env`; throws one readable error listing every invalid variable (never their values). */
export function parseEnv(env: NodeJS.ProcessEnv): AppConfig {
  const result = envSchema.safeParse(env);
  if (!result.success) {
    const problems = result.error.issues.map((i) => `  ${i.path.join('.')}: ${i.message}`);
    throw new Error(`Invalid environment configuration:\n${problems.join('\n')}`);
  }
  return result.data;
}
