/**
 * Everything the product needs, in one command: `pnpm demo`.
 *
 * Three processes, not one, and the reason matters. The workers are a long-running consumer
 * sitting on a Redis stream; without them a research job is created and nothing ever runs it, so
 * the screen shows a job that never finishes and no leads at all. That is also why this stack
 * cannot go on Vercel alone — serverless has no process to sit on a stream.
 *
 * Ctrl+C stops all three.
 */
import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { createConnection } from 'node:net';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const envFile = resolve(root, '.env');

const RESET = '\u001b[0m';
const COLOURS = { redis: '\u001b[35m', workers: '\u001b[36m', app: '\u001b[32m' };

function say(message) {
  process.stdout.write(`${message}\n`);
}

function preflight() {
  if (!existsSync(envFile)) {
    say('\n.env is missing. See infra/setup/SETUP.md — the demo needs the database and API keys.');
    process.exit(1);
  }
  const env = readFileSync(envFile, 'utf8');
  const missing = ['DATABASE_URL_WORKERS', 'REDIS_URL', 'API_URL', 'APP_URL'].filter(
    (key) => !new RegExp(`^${key}=.+`, 'm').test(env),
  );
  if (missing.length > 0) {
    say(`\n.env is missing ${missing.join(', ')}. See infra/setup/SETUP.md.`);
    process.exit(1);
  }
  if (!/^GOOGLE_PLACES_ENABLED=true/m.test(env)) {
    // Not fatal: everything else works, and a run will simply report that no source can find
    // businesses. Better said out loud now than discovered in front of an audience.
    say(
      '\n  Note: GOOGLE_PLACES_ENABLED is not true in .env, so a new search will find nothing.\n' +
        '  Existing jobs and their leads still open fine. Set it to true to run live searches;\n' +
        '  each search costs about $0.035 of Google Places.\n',
    );
  }
}

function waitForPort(port, host, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolveWait, rejectWait) => {
    const attempt = () => {
      const socket = createConnection({ port, host });
      socket.once('connect', () => {
        socket.end();
        resolveWait();
      });
      socket.once('error', () => {
        socket.destroy();
        if (Date.now() > deadline) rejectWait(new Error(`nothing listening on ${host}:${port}`));
        else setTimeout(attempt, 300);
      });
    };
    attempt();
  });
}

const children = [];

function start(name, command, args, { env: extraEnv = {}, cwd = root } = {}) {
  const child = spawn(command, args, {
    cwd,
    shell: true,
    env: { ...process.env, ...extraEnv },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const prefix = `${COLOURS[name]}[${name}]${RESET} `;
  const forward = (stream) => {
    let buffer = '';
    stream.on('data', (chunk) => {
      buffer += chunk.toString();
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      for (const line of lines) if (line.trim()) say(prefix + line);
    });
  };
  forward(child.stdout);
  forward(child.stderr);
  child.on('exit', (code) => {
    if (!stopping && code !== 0) {
      say(`\n${prefix}stopped with code ${code}. Shutting the rest down.`);
      stop();
    }
  });
  children.push(child);
  return child;
}

let stopping = false;
function stop() {
  if (stopping) return;
  stopping = true;
  say('\nStopping…');
  for (const child of children) {
    if (child.pid && !child.killed) {
      // Windows needs the whole tree: pnpm spawns node, which spawns next/uv.
      if (process.platform === 'win32') spawn('taskkill', ['/pid', String(child.pid), '/T', '/F']);
      else child.kill('SIGTERM');
    }
  }
  setTimeout(() => process.exit(0), 1500);
}

process.on('SIGINT', stop);
process.on('SIGTERM', stop);

async function main() {
  preflight();

  say('Starting Redis…');
  start('redis', 'pnpm', ['redis:start']);
  await waitForPort(6379, '127.0.0.1', 30_000).catch((err) => {
    say(`Redis did not start: ${err.message}`);
    stop();
  });

  say('Starting the workers (planner + discovery)…');
  start('workers', 'uv', ['run', 'python', '-m', 'app.main'], {
    cwd: resolve(root, 'services', 'workers'),
    env: { WORKER_POOLS: 'system,interactive,discovery', PYTHONIOENCODING: 'utf-8' },
  });

  say('Starting the web app and API…');
  start('app', 'pnpm', ['dev']);
  await waitForPort(3000, '127.0.0.1', 180_000).catch(() => {
    say('The web app is taking a while — it may still be building.');
  });

  say(
    `\n${'-'.repeat(64)}\n` +
      '  Open http://localhost:3000/research/new\n' +
      '  Ctrl+C stops everything.\n' +
      `${'-'.repeat(64)}\n`,
  );
}

main().catch((err) => {
  say(String(err));
  stop();
});
