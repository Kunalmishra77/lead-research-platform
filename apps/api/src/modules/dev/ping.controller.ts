import {
  Body,
  Controller,
  Get,
  HttpCode,
  Inject,
  Param,
  Post,
  Req,
  Res,
  UseGuards,
} from '@nestjs/common';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';
import type { FastifyReply, FastifyRequest } from 'fastify';
import { PinoLogger } from 'nestjs-pino';

import { CurrentTenant, CurrentUser } from '../../common/current-request.decorator';
import { RequirePermission } from '../../common/rbac/require-permission.decorator';
import { ProgressHub } from '../../common/sse/progress-hub';
import { streamProgress, TERMINAL_STATUSES } from '../../common/sse/progress-stream';
import { APP_CONFIG } from '../../config/config.module';
import type { AppConfig } from '../../config/env.schema';
import type { AuthUser, TenantInfo } from '../auth/auth.types';
import { DevOnlyGuard } from './dev-only.guard';
import { CreatePingDto, JobIdParamDto, type JobRunView } from './ping.dto';
import { PingService } from './ping.service';

/** Round-trip demo for the job pipeline (task 1.10). Not mounted in production. */
@ApiTags('dev')
@ApiBearerAuth()
@UseGuards(DevOnlyGuard)
@Controller('app/dev/ping-job')
export class PingController {
  constructor(
    private readonly ping: PingService,
    private readonly hub: ProgressHub,
    @Inject(APP_CONFIG) private readonly config: AppConfig,
    private readonly logger: PinoLogger,
  ) {
    this.logger.setContext(PingController.name);
  }

  @Post()
  @HttpCode(202)
  @RequirePermission('research.run')
  create(
    @CurrentUser() user: AuthUser,
    @CurrentTenant() tenant: TenantInfo,
    @Body() body: CreatePingDto,
  ): Promise<{ jobId: string; status: 'queued' }> {
    return this.ping.create(user, tenant, body);
  }

  @Get(':id')
  @RequirePermission('research.run')
  get(
    @CurrentUser() user: AuthUser,
    @CurrentTenant() tenant: TenantInfo,
    @Param() params: JobIdParamDto,
  ): Promise<JobRunView> {
    return this.ping.get(user, tenant, params.id);
  }

  /** SSE: `state`, then `progress` events, then `done` (docs/05 SSE progress). */
  @Get(':id/events')
  @RequirePermission('research.run')
  async events(
    @CurrentUser() user: AuthUser,
    @CurrentTenant() tenant: TenantInfo,
    @Param() params: JobIdParamDto,
    @Req() request: FastifyRequest,
    @Res() reply: FastifyReply,
  ): Promise<void> {
    await this.ping.get(user, tenant, params.id); // authorizes (404 before any SSE header)
    await streamProgress(request, reply, this.hub, this.logger, {
      channel: `progress:${params.id}`,
      userId: user.userId,
      loadState: () => this.ping.get(user, tenant, params.id),
      isTerminal: (state) => TERMINAL_STATUSES.has(state.status),
      maxDurationMs: this.config.PROGRESS_STREAM_MAX_MS,
    });
  }
}
