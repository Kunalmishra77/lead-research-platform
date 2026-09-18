import 'server-only';

import { z } from 'zod';

/**
 * Server-only configuration. Supabase settings are deliberately NOT `NEXT_PUBLIC_`: the browser
 * never talks to supabase.co (ADR-0002); all auth calls run on this server.
 */
const schema = z.object({
  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
  APP_URL: z.url(),
  API_URL: z.url(),
  SUPABASE_URL: z.url(),
  SUPABASE_PUBLISHABLE_KEY: z.string().min(20),
});

export type WebEnv = z.infer<typeof schema>;

let cached: WebEnv | undefined;

export function env(): WebEnv {
  if (!cached) {
    const result = schema.safeParse(process.env);
    if (!result.success) {
      const problems = result.error.issues.map((i) => `${i.path.join('.')}: ${i.message}`);
      throw new Error(
        `Invalid web configuration (see infra/setup/SETUP.md):\n${problems.join('\n')}`,
      );
    }
    cached = result.data;
  }
  return cached;
}
