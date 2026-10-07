import { Controller, Get, Param, ParseUUIDPipe, Query, Req } from '@nestjs/common';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';
import type { FastifyRequest } from 'fastify';

import { CurrentUser } from '../../common/current-request.decorator';
import { requestOrigin } from '../../common/request-origin';
import type { AuthUser } from '../auth/auth.types';
import {
  type AdminConnector,
  type AdminJob,
  AdminJobsQueryDto,
  AdminListQueryDto,
  type AdminMeter,
  type AdminOrg,
  AdminSinceQueryDto,
  type AdminTask,
  type AdminUser,
  type Page,
} from './admin.dto';
import { AdminService } from './admin.service';

/**
 * Platform staff only (docs/05 `/admin/*`). Read-only lists across all orgs; every call is audited
 * by the SECURITY DEFINER functions, which also enforce the staff flag (ADR-0003).
 */
@ApiTags('admin')
@ApiBearerAuth()
@Controller('admin')
export class AdminController {
  constructor(private readonly admin: AdminService) {}

  @Get('orgs')
  orgs(
    @CurrentUser() user: AuthUser,
    @Query() query: AdminListQueryDto,
    @Req() request: FastifyRequest,
  ): Promise<Page<AdminOrg>> {
    return this.admin.listOrgs(user, requestOrigin(request), query.limit, query.cursor);
  }

  @Get('users')
  users(
    @CurrentUser() user: AuthUser,
    @Query() query: AdminListQueryDto,
    @Req() request: FastifyRequest,
  ): Promise<Page<AdminUser>> {
    return this.admin.listUsers(user, requestOrigin(request), query.limit, query.cursor);
  }

  @Get('jobs')
  jobs(
    @CurrentUser() user: AuthUser,
    @Query() query: AdminJobsQueryDto,
    @Req() request: FastifyRequest,
  ): Promise<Page<AdminJob>> {
    return this.admin.listJobs(
      user,
      requestOrigin(request),
      query.limit,
      query.cursor,
      query.status,
    );
  }

  @Get('jobs/:id/tasks')
  jobTasks(
    @CurrentUser() user: AuthUser,
    @Param('id', new ParseUUIDPipe({ version: '7' })) id: string,
    @Req() request: FastifyRequest,
  ): Promise<AdminTask[]> {
    return this.admin.jobTasks(user, requestOrigin(request), id);
  }

  @Get('connectors')
  connectors(
    @CurrentUser() user: AuthUser,
    @Query() query: AdminSinceQueryDto,
    @Req() request: FastifyRequest,
  ): Promise<AdminConnector[]> {
    return this.admin.connectorHealth(user, requestOrigin(request), query.since);
  }

  @Get('usage')
  usage(
    @CurrentUser() user: AuthUser,
    @Query() query: AdminSinceQueryDto,
    @Req() request: FastifyRequest,
  ): Promise<AdminMeter[]> {
    return this.admin.usageByMeter(user, requestOrigin(request), query.since);
  }
}
