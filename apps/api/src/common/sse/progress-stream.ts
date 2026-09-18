import type { FastifyReply, FastifyRequest } from 'fastify';
import type { PinoLogger } from 'nestjs-pino';

import { AppError } from '../errors/app-error';
import { REQUEST_ID_HEADER } from '../request-id';
import type { ProgressHub } from './progress-hub';

export const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled', 'paused']);
const DEFAULT_HEARTBEAT_MS = 15_000;
const DEFAULT_MAX_DURATION_MS = 10 * 60_000;

export interface ProgressStreamOptions<S> {
  /** Redis pub/sub channel, e.g. `progress:{jobId}` (docs/01). */
  channel: string;
  /** Caller, for the per-user stream cap. */
  userId: string;
  /** Persisted state (Postgres): sent first, polled on heartbeats, sent again as `done`. */
  loadState: () => Promise<S>;
  isTerminal: (state: S) => boolean;
  heartbeatMs?: number;
  maxDurationMs?: number;
}

/**
 * SSE progress relay (docs/05): `state` (persisted) -> live `progress` events -> `done`.
 * - Subscribes before reading the state; events arriving before `state` is sent are buffered, so
 *   the order is always state -> progress -> done.
 * - Every heartbeat re-reads the persisted state, so a terminal event lost during a Redis reconnect
 *   still ends the stream.
 * - The caller must authorize before calling this: headers are sent right away.
 */
export async function streamProgress<S>(
  request: FastifyRequest,
  reply: FastifyReply,
  hub: ProgressHub,
  logger: PinoLogger,
  options: ProgressStreamOptions<S>,
): Promise<void> {
  const release = hub.acquire(options.userId);
  const buffered: string[] = [];
  let onMessage: (message: string) => void = (message) => buffered.push(message);
  let unlisten: (() => Promise<void>) | undefined;
  try {
    unlisten = await hub.listen(options.channel, (message) => {
      onMessage(message);
    });
  } catch (err) {
    release();
    throw new AppError({
      code: 'progress.unavailable',
      httpStatus: 503,
      title: 'Live progress temporarily unavailable',
      errorClass: 'transient',
      cause: err,
    });
  }

  reply.hijack();
  const res = reply.raw;
  let closed = false;
  let finishing = false;
  let stateSent = false;
  const close = (): void => {
    if (closed) return;
    closed = true;
    clearInterval(heartbeat);
    clearTimeout(deadline);
    release();
    if (!res.writableEnded) res.end();
    void unlisten();
  };
  const send = (event: string, data: unknown): void => {
    if (!closed && !res.destroyed) res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
  };
  const finish = async (): Promise<void> => {
    if (finishing || closed) return;
    finishing = true;
    try {
      send('done', await options.loadState());
    } catch (err) {
      logger.error({ err, error_class: 'transient' }, 'progress stream: final state failed');
    }
    close();
  };
  const handle = (message: string): void => {
    let event: { status?: unknown };
    try {
      event = JSON.parse(message) as { status?: unknown };
    } catch {
      logger.warn({ error_class: 'parse_failed' }, 'progress stream: unparsable event dropped');
      return;
    }
    send('progress', event);
    if (typeof event.status === 'string' && TERMINAL_STATUSES.has(event.status)) void finish();
  };

  res.on('close', close); // client went away (also covers disconnects during setup)
  const heartbeat = setInterval(() => {
    if (closed) return;
    res.write(': heartbeat\n\n');
    if (!stateSent) return; // `done` must never precede `state`
    options.loadState().then(
      (state) => {
        if (options.isTerminal(state)) void finish();
      },
      () => undefined,
    );
  }, options.heartbeatMs ?? DEFAULT_HEARTBEAT_MS);
  const deadline = setTimeout(close, options.maxDurationMs ?? DEFAULT_MAX_DURATION_MS);
  if (res.destroyed) {
    close();
    return;
  }

  res.writeHead(200, {
    'content-type': 'text/event-stream; charset=utf-8',
    'cache-control': 'no-cache, no-transform',
    connection: 'keep-alive',
    'x-accel-buffering': 'no',
    // Hijacked replies skip Fastify's onSend hook.
    [REQUEST_ID_HEADER]: request.id,
  });

  try {
    const state = await options.loadState();
    send('state', state);
    stateSent = true;
    onMessage = handle;
    for (const message of buffered.splice(0)) handle(message);
    if (options.isTerminal(state)) await finish();
  } catch (err) {
    logger.error({ err, error_class: 'transient' }, 'progress stream: initial state failed');
    close();
  }
}
