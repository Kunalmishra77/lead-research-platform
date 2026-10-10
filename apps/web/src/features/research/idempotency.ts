import type { ResearchSpec } from '@leadforge/contracts';

/**
 * What one submission of the run form sends as its Idempotency-Key.
 *
 * The API replays a repeated key for 24 hours. That is right for a double-clicked button and
 * wrong for "run this search again tomorrow", so what the key is built from decides which of
 * the two the product supports. The first version hashed the content alone, which chose the
 * second: asking for the same leads twice in a day silently returned the first run's job and
 * did no work at all.
 *
 * So the key is a visit nonce *and* a content hash. Two clicks of the same reviewed request in
 * one visit share a key and the second is ignored, which is what the key was added for. A later
 * visit, or an edited chip or depth, is a different request and earns a new one.
 */
export function idempotencyKeyFor(visit: string, rawQuery: string, spec: ResearchSpec): string {
  return `web:${visit}:${hash(JSON.stringify({ rawQuery, spec }))}`;
}

/**
 * A short random string, minted once per visit to the form.
 *
 * Not `crypto.randomUUID()`: that needs a secure context and this app is reachable over plain
 * HTTP, where it is simply undefined. Nothing here is a secret — it only has to differ between
 * one visit and the next.
 */
export function nonce(): string {
  return Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}

/** FNV-1a. Short, stable, and not a security boundary — it only has to vary with the input. */
function hash(input: string): string {
  let h = 2166136261;
  for (let i = 0; i < input.length; i += 1) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(36);
}
