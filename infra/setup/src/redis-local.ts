/**
 * Downloads (once) and runs a portable Redis for Windows dev machines without Docker (ADR-0002).
 * Usage: pnpm redis:start   (keeps running in the foreground; Ctrl+C to stop)
 */
import { execFileSync, spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const VERSION = '8.10.2';
const ASSET = `Redis-${VERSION}-Windows-x64-msys2`;
const URL = `https://github.com/redis-windows/redis-windows/releases/download/${VERSION}/${ASSET}.zip`;
const SHA256 = '7c8cebd50347eaa1d9e784da842ed47a4f33394637835531d6614777b950ee85';

if (process.platform !== 'win32') {
  console.error('redis-local: use your OS package manager (e.g. brew/apt install redis) instead.');
  process.exit(1);
}

const home = join(
  process.env.LOCALAPPDATA ?? join(process.env.USERPROFILE ?? '.', 'AppData', 'Local'),
  'leadforge',
  'redis',
);
const server = join(home, ASSET, 'redis-server.exe');

if (!existsSync(server)) {
  mkdirSync(home, { recursive: true });
  const zip = join(home, `${ASSET}.zip`);
  console.log(`redis-local: downloading Redis ${VERSION} ...`);
  const res = await fetch(URL);
  if (!res.ok) throw new Error(`download failed: HTTP ${String(res.status)}`);
  writeFileSync(zip, Buffer.from(await res.arrayBuffer()));
  const digest = createHash('sha256').update(readFileSync(zip)).digest('hex');
  if (digest !== SHA256) throw new Error(`checksum mismatch for ${zip}: ${digest}`);
  const tar = join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'tar.exe');
  execFileSync(tar, ['-xf', zip, '-C', home], { stdio: 'inherit' });
}

const dataDir = join(home, 'data');
mkdirSync(dataDir, { recursive: true });
console.log(`redis-local: starting ${server} on 127.0.0.1:6379 (data in ${dataDir})`);
const child = spawn(
  server,
  [
    '--port',
    '6379',
    '--bind',
    '127.0.0.1',
    '--protected-mode',
    'yes',
    '--dir',
    dataDir,
    '--appendonly',
    'yes',
    '--save',
    '',
  ],
  { stdio: 'inherit' },
);
child.on('exit', (code, signal) => process.exit(code ?? (signal === null ? 0 : 1)));
