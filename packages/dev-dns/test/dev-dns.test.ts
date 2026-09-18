import dns from 'node:dns';
import { promisify } from 'node:util';

import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { installDevDns, installDevDnsFromEnv } from '../src/index.js';

function lookup(host: string, options?: dns.LookupOptions | number): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const cb = (err: NodeJS.ErrnoException | null, address: unknown) => {
      if (err) reject(err);
      else resolve(address);
    };
    if (options === undefined) dns.lookup(host, cb);
    else if (typeof options === 'number') dns.lookup(host, options, cb);
    else dns.lookup(host, options, cb);
  });
}

function answer(...ips: string[]): Response {
  return new Response(JSON.stringify({ Answer: ips.map((data) => ({ type: 1, data, TTL: 60 })) }));
}

describe('installDevDnsFromEnv', () => {
  it('only activates for development or test with the flag set', () => {
    expect(installDevDnsFromEnv({})).toBe(false);
    expect(installDevDnsFromEnv({ DEV_DNS_OVER_HTTPS: 'true' })).toBe(false);
    expect(installDevDnsFromEnv({ DEV_DNS_OVER_HTTPS: 'true', NODE_ENV: 'production' })).toBe(
      false,
    );
    expect(installDevDnsFromEnv({ DEV_DNS_OVER_HTTPS: 'true', NODE_ENV: 'staging' })).toBe(false);
  });
});

describe('installDevDns', () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeAll(() => {
    vi.stubGlobal('fetch', fetchMock);
    installDevDns(['.blocked.test']);
  });

  afterEach(() => {
    fetchMock.mockReset();
  });

  it('resolves matching hosts over DoH (callback as 2nd argument)', async () => {
    fetchMock.mockResolvedValue(answer('104.18.38.10'));
    await expect(lookup('abc.blocked.test')).resolves.toBe('104.18.38.10');
    expect(fetchMock.mock.calls[0]?.[0] as string).toContain('name=abc.blocked.test');
  });

  it('normalizes case and trailing dots', async () => {
    fetchMock.mockResolvedValue(answer('1.1.1.1'));
    await expect(lookup('UPPER.Blocked.Test.')).resolves.toBe('1.1.1.1');
    expect(fetchMock.mock.calls[0]?.[0] as string).toContain('name=upper.blocked.test');
  });

  it('accepts a numeric family argument', async () => {
    fetchMock.mockResolvedValue(answer('9.9.9.9'));
    await expect(lookup('num.blocked.test', 4)).resolves.toBe('9.9.9.9');
  });

  it('returns all A records when asked, ignoring other record types', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          Answer: [
            { type: 5, data: 'alias.example.' },
            { type: 1, data: '1.2.3.4' },
            { type: 1, data: '5.6.7.8' },
          ],
        }),
      ),
    );
    await expect(lookup('all.blocked.test', { all: true })).resolves.toEqual([
      { address: '1.2.3.4', family: 4 },
      { address: '5.6.7.8', family: 4 },
    ]);
  });

  it('never labels IPv4 answers as IPv6', async () => {
    await expect(lookup('v6.blocked.test', { family: 6 })).rejects.toMatchObject({
      code: 'ENOTFOUND',
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('reports ENOTFOUND when DoH has no A record', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ Answer: [] })));
    await expect(lookup('none.blocked.test')).rejects.toMatchObject({ code: 'ENOTFOUND' });
  });

  it('reports EAI_AGAIN (transient) when the DoH endpoint fails', async () => {
    fetchMock.mockResolvedValue(new Response('nope', { status: 503 }));
    await expect(lookup('down.blocked.test')).rejects.toMatchObject({ code: 'EAI_AGAIN' });
    fetchMock.mockRejectedValue(new Error('timeout'));
    await expect(lookup('timeout.blocked.test')).rejects.toMatchObject({ code: 'EAI_AGAIN' });
  });

  it('supports util.promisify and dns.promises', async () => {
    fetchMock.mockImplementation(() => Promise.resolve(answer('7.7.7.7')));
    await expect(promisify(dns.lookup)('p1.blocked.test')).resolves.toEqual({
      address: '7.7.7.7',
      family: 4,
    });
    await expect(dns.promises.lookup('p2.blocked.test')).resolves.toEqual({
      address: '7.7.7.7',
      family: 4,
    });
  });

  it('leaves other hosts on the system resolver', async () => {
    await expect(lookup('localhost')).resolves.toBeTypeOf('string');
    await expect(dns.promises.lookup('localhost')).resolves.toHaveProperty('address');
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
