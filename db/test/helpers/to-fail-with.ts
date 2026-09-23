/**
 * `expect(promise).toFailWith(/pattern/)` matched against the ROOT cause message: Drizzle wraps
 * driver errors ("Failed query: ..."), so asserting on the wrapper would hide the real reason.
 */
import { expect } from 'vitest';

function rootMessage(err: unknown): string {
  let e: unknown = err;
  while (e instanceof Error && e.cause !== undefined) e = e.cause;
  return e instanceof Error ? e.message : String(e);
}

expect.extend({
  async toFailWith(received: Promise<unknown>, pattern: RegExp) {
    try {
      await received;
    } catch (err) {
      const message = rootMessage(err);
      return {
        pass: pattern.test(message),
        message: () => `expected failure matching ${String(pattern)}, got: ${message}`,
      };
    }
    return {
      pass: false,
      message: () => `expected failure matching ${String(pattern)}, but it succeeded`,
    };
  },
});

declare module 'vitest' {
  // Must repeat Vitest 5's exact type parameters to merge.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface Matchers<R extends void | Promise<void> = void | Promise<void>, T = unknown> {
    toFailWith(pattern: RegExp): Promise<void>;
  }
}
