import { Controller, Get, Query, Req } from '@nestjs/common';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';
import type { FastifyRequest } from 'fastify';

import { CurrentUser } from '../../common/current-request.decorator';
import { requestOrigin } from '../../common/request-origin';
import type { AuthUser } from '../auth/auth.types';
import { AdminListQueryDto, type AdminOrg, type AdminUser, type Page } from './admin.dto';
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
}
