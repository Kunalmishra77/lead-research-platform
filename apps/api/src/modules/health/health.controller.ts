import { Controller, Get, HttpCode, Res } from '@nestjs/common';
import { ApiOkResponse, ApiServiceUnavailableResponse, ApiTags } from '@nestjs/swagger';
import type { FastifyReply } from 'fastify';

import { HealthService, type Readiness } from './health.service';

@ApiTags('health')
@Controller('health')
export class HealthController {
  constructor(private readonly health: HealthService) {}

  /** Process is up. Never touches dependencies (used by liveness probes). */
  @Get('live')
  @HttpCode(200)
  @ApiOkResponse({ description: 'Process is alive' })
  live(): { status: 'ok' } {
    return { status: 'ok' };
  }

  /** Dependencies reachable: DB, Redis, object storage. 503 when any check fails. */
  @Get('ready')
  @ApiOkResponse({ description: 'All dependencies reachable' })
  @ApiServiceUnavailableResponse({ description: 'At least one dependency failed' })
  async ready(@Res({ passthrough: true }) reply: FastifyReply): Promise<Readiness> {
    const result = await this.health.readiness();
    void reply.status(result.status === 'ok' ? 200 : 503);
    return result;
  }
}
