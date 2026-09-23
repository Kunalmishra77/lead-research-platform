import {
  Body,
  Controller,
  Get,
  HttpCode,
  Inject,
  Param,
  Post,
  Query,
  Req,
  Res,
} from '@nestjs/common';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';
import type { FastifyReply, FastifyRequest } from 'fastify';
import { PinoLogger } from 'nestjs-pino';

import { CurrentTenant, CurrentUser } from '../../common/current-request.decorator';
import { IdempotencyService } from '../../common/idempotency/idempotency.service';
import { RequirePermission } from '../../common/rbac/require-permission.decorator';
import { ProgressHub } from '../../common/sse/progress-hub';
import { streamProgress, TERMINAL_STATUSES } from '../../common/sse/progress-stream';
import { APP_CONFIG } from '../../config/config.module';
import type { AppConfig } from '../../config/env.schema';
import type { AuthUser, TenantInfo } from '../auth/auth.types';
import type { ParseResultView } from './parse.service';
import { ParseService } from './parse.service';
import {
  CreateResearchDto,
  JobIdParamDto,
  ParseResearchDto,
  type ResearchJobView,
  ResearchListQueryDto,
  type ResearchPage,
} from './research.dto';
import { ResearchService } from './research.service';

/** Research runs (docs/05 research endpoints). Spending credits needs `research.run`. */
@ApiTags('research')
@ApiBearerAuth()
@Controller('app/research')
export class ResearchController {
  constructor(
    private readonly research: ResearchService,
    private readonly parser: ParseService,
    private readonly idempotency: IdempotencyService,
    private readonly hub: ProgressHub,
    @Inject(APP_CONFIG) private readonly config: AppConfig,
    private readonly logger: PinoLogger,
  ) {
    this.logger.setContext(ResearchController.name);
  }

  /**
   * 202 + job id; credits are reserved before the job is queued (402 when short). Send
   * `Idempotency-Key` to make a retry replay the first answer instead of spending again (docs/05).
   */
  @Post()
  @HttpCode(202)
  @RequirePermission('research.run')
  create(
    @CurrentUser() user: AuthUser,
    @CurrentTenant() tenant: TenantInfo,
    @Body() body: CreateResearchDto,
    @Req() request: FastifyRequest,
  ): Promise<{ jobId: string; status: 'queued'; credits: { reserved: number; balance: number } }> {
    const key = request.headers['idempotency-key'];
    return this.idempotency.run(
      {
        orgId: tenant.orgId,
        route: 'research.create',
        key: typeof key === 'string' ? key : undefined,
      },
      () =>
        this.research.create(user, tenant, {
          rawQuery: body.rawQuery,
          spec: body.spec,
          traceId: request.id,
        }),
    );
  }

  /**
   * Reads a sentence into a spec the user can correct before anything is spent (ADR-0005).
   * Charges no credits, but does cost model tokens, so it is rate limited per workspace and
   * needs the same permission as running a search.
   */
  @Post('parse')
  @HttpCode(200)
  @RequirePermission('research.run')
  parse(
    @CurrentUser() user: AuthUser,
    @CurrentTenant() tenant: TenantInfo,
    @Body() body: ParseResearchDto,
    @Req() request: FastifyRequest,
  ): Promise<ParseResultView> {
    return this.parser.parse(tenant, user, {
      rawQuery: body.rawQuery,
      traceId: request.id,
    });
  }

  /** History of the active workspace, newest first. Viewers may read it. */
  @Get()
  @RequirePermission('contacts.view')
  list(
    @CurrentUser() user: AuthUser,
    @CurrentTenant() tenant: TenantInfo,
    @Query() query: ResearchListQueryDto,
  ): Promise<ResearchPage> {
    return this.research.list(user, tenant, query);
  }

  @Get(':id')
  @RequirePermission('contacts.view')
  get(
    @CurrentUser() user: AuthUser,
    @CurrentTenant() tenant: TenantInfo,
    @Param() params: JobIdParamDto,
  ): Promise<ResearchJobView> {
    return this.research.get(user, tenant, params.id);
  }

  /** SSE: `state`, then live `progress` events, then `done` (docs/05 SSE progress). */
  @Get(':id/events')
  @RequirePermission('contacts.view')
  async events(
    @CurrentUser() user: AuthUser,
    @CurrentTenant() tenant: TenantInfo,
    @Param() params: JobIdParamDto,
    @Req() request: FastifyRequest,
    @Res() reply: FastifyReply,
  ): Promise<void> {
    await this.research.get(user, tenant, params.id); // authorizes (404 before any SSE header)
    await streamProgress(request, reply, this.hub, this.logger, {
      channel: `progress:${params.id}`,
      userId: user.userId,
      loadState: () => this.research.get(user, tenant, params.id),
      isTerminal: (state) => TERMINAL_STATUSES.has(state.status),
      maxDurationMs: this.config.PROGRESS_STREAM_MAX_MS,
    });
  }

  /** Stops the run and returns the unused credits. Spending rights are needed to stop spending. */
  @Post(':id/cancel')
  @HttpCode(200)
  @RequirePermission('research.run')
  cancel(
    @CurrentUser() user: AuthUser,
    @CurrentTenant() tenant: TenantInfo,
    @Param() params: JobIdParamDto,
  ): Promise<ResearchJobView> {
    return this.research.cancel(user, tenant, params.id);
  }
}
