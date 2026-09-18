/**
 * Dev-only DNS-over-HTTPS resolution for hosts that some Indian ISPs sinkhole (e.g. *.supabase.co,
 * see ADR-0002). Patches `dns.lookup` and `dns.promises.lookup`, which Node's net/tls, `fetch`
 * (undici) and the AWS SDK use. Only A records are resolved. Never enabled outside dev/test.
 */
import dns from 'node:dns';
import { promisify } from 'node:util';

const DEFAULT_SUFFIXES = ['.supabase.co'];
const DOH_ENDPOINT = 'https://1.1.1.1/dns-query';
const MAX_TTL_MS = 5 * 60 * 1000;
const NEGATIVE_TTL_MS = 30 * 1000;

interface DohAnswer {
  type: number;
  data: string;
  TTL?: number;
}

type LookupCallback = (
  err: NodeJS.ErrnoException | null,
  address: string | dns.LookupAddress[],
  family?: number,
) => void;

const cache = new Map<string, { ips: string[]; expires: number }>();
const inflight = new Map<string, Promise<string[]>>();
let installed = false;

function dnsError(code: 'ENOTFOUND' | 'EAI_AGAIN', host: string, cause?: unknown): Error {
  const err: NodeJS.ErrnoException = new Error(`getaddrinfo ${code} ${host}`, { cause });
  err.code = code;
  err.syscall = 'getaddrinfo';
  return err;
}

async function queryDoh(host: string): Promise<string[]> {
  let body: { Answer?: DohAnswer[] };
  try {
    const res = await fetch(`${DOH_ENDPOINT}?name=${encodeURIComponent(host)}&type=A`, {
      headers: { accept: 'application/dns-json' },
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) throw new Error(`DoH HTTP ${String(res.status)}`);
    body = (await res.json()) as { Answer?: DohAnswer[] };
  } catch (err) {
    throw dnsError('EAI_AGAIN', host, err);
  }
  const answers = (body.Answer ?? []).filter((a) => a.type === 1);
  const ttlSeconds = Math.min(...answers.map((a) => a.TTL ?? 300), 300);
  const ttl = answers.length > 0 ? Math.min(ttlSeconds * 1000, MAX_TTL_MS) : NEGATIVE_TTL_MS;
  const ips = answers.map((a) => a.data);
  cache.set(host, { ips, expires: Date.now() + ttl });
  return ips;
}

/** Resolves A records for `host` over DoH (cached by TTL, concurrent calls deduplicated). */
export async function resolveOverHttps(host: string): Promise<string[]> {
  const hit = cache.get(host);
  if (hit && hit.expires > Date.now()) return hit.ips;
  const pending = inflight.get(host);
  if (pending) return pending;
  const request = queryDoh(host).finally(() => inflight.delete(host));
  inflight.set(host, request);
  return request;
}

function normalize(host: string): string {
  return host.toLowerCase().replace(/\.$/, '');
}

function toOptions(options: unknown): dns.LookupOptions {
  if (typeof options === 'number') return { family: options };
  if (typeof options === 'object' && options !== null) return options;
  return {};
}

async function lookupOverHttps(
  host: string,
  opts: dns.LookupOptions,
): Promise<{ address: string | dns.LookupAddress[]; family: number }> {
  const family = opts.family === 'IPv6' ? 6 : opts.family === 'IPv4' ? 4 : opts.family;
  if (family === 6) throw dnsError('ENOTFOUND', host);
  const ips = await resolveOverHttps(host);
  const first = ips[0];
  if (first === undefined) throw dnsError('ENOTFOUND', host);
  if (opts.all === true) {
    return { address: ips.map((address) => ({ address, family: 4 })), family: 4 };
  }
  return { address: first, family: 4 };
}

/** Routes lookups for hosts ending in one of `suffixes` through DoH. Idempotent. */
export function installDevDns(suffixes: string[] = DEFAULT_SUFFIXES): void {
  if (installed) return;
  installed = true;
  const matches = (host: unknown): host is string =>
    typeof host === 'string' && suffixes.some((s) => normalize(host).endsWith(s));
  const originalLookup = dns.lookup.bind(dns) as (...args: unknown[]) => void;
  const originalPromiseLookup = dns.promises.lookup.bind(dns.promises) as (
    ...args: unknown[]
  ) => Promise<unknown>;

  function patched(host: unknown, options: unknown, callback?: LookupCallback): void {
    const cb = (typeof options === 'function' ? options : callback) as LookupCallback;
    const opts = toOptions(typeof options === 'function' ? undefined : options);
    if (!matches(host)) {
      originalLookup(host, opts, cb);
      return;
    }
    lookupOverHttps(normalize(host), opts).then(
      (r) => {
        cb(null, r.address, r.family);
      },
      (err: unknown) => {
        cb(err as NodeJS.ErrnoException, '');
      },
    );
  }

  const promiseLookup = (host: unknown, options?: unknown): Promise<unknown> =>
    matches(host)
      ? lookupOverHttps(normalize(host), toOptions(options)).then((r) =>
          Array.isArray(r.address) ? r.address : { address: r.address, family: r.family },
        )
      : originalPromiseLookup(host, options);

  Object.defineProperty(patched, promisify.custom, { value: promiseLookup });
  (dns as { lookup: unknown }).lookup = patched;
  (dns.promises as { lookup: unknown }).lookup = promiseLookup;
}

/** Installs the patch only when DEV_DNS_OVER_HTTPS=true and NODE_ENV is development or test. */
export function installDevDnsFromEnv(env: NodeJS.ProcessEnv = process.env): boolean {
  const devLike = env.NODE_ENV === 'development' || env.NODE_ENV === 'test';
  if (env.DEV_DNS_OVER_HTTPS !== 'true' || !devLike) return false;
  installDevDns();
  return true;
}
