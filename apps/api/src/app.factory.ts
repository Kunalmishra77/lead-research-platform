import type { Type } from '@nestjs/common';
import { NestFactory } from '@nestjs/core';
import { FastifyAdapter, type NestFastifyApplication } from '@nestjs/platform-fastify';
import { Logger } from 'nestjs-pino';

import { generateRequestId, REQUEST_ID_HEADER } from './common/request-id';

const BODY_LIMIT_BYTES = 1024 * 1024;

/**
 * @param trustProxy hop count or proxy CIDRs from TRUST_PROXY (never `true`: that would let clients
 * spoof request.ip, which audit rows record, via X-Forwarded-For).
 */
export function createAdapter(trustProxy: number | string[] | false = false): FastifyAdapter {
  return new FastifyAdapter({
    genReqId: generateRequestId,
    bodyLimit: BODY_LIMIT_BYTES,
    trustProxy,
  });
}

/** Cross-cutting HTTP setup shared by main.ts and the e2e tests. */
export function configureApp(app: NestFastifyApplication): void {
  app.useLogger(app.get(Logger));
  app.enableShutdownHooks();
  app
    .getHttpAdapter()
    .getInstance()
    .addHook('onSend', async (request, reply) => {
      void reply.header(REQUEST_ID_HEADER, request.id);
    });
}

export async function createApp(
  module: Type,
  trustProxy: number | string[] | false = false,
): Promise<NestFastifyApplication> {
  const app = await NestFactory.create<NestFastifyApplication>(module, createAdapter(trustProxy), {
    bufferLogs: true,
  });
  configureApp(app);
  return app;
}
