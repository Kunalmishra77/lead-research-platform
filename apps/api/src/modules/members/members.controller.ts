import { Body, Controller, Get, Param, Patch, Req } from '@nestjs/common';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';
import type { FastifyRequest } from 'fastify';

import { CurrentTenant, CurrentUser } from '../../common/current-request.decorator';
import { RequirePermission } from '../../common/rbac/require-permission.decorator';
import { requestOrigin } from '../../common/request-origin';
import type { AuthUser, TenantInfo } from '../auth/auth.types';
import { ChangeRoleDto, type Member, UserIdParamDto } from './members.dto';
import { MembersService } from './members.service';

@ApiTags('team')
@ApiBearerAuth()
@Controller('app/members')
export class MembersController {
  constructor(private readonly members: MembersService) {}

  /** Members of the active workspace (X-Workspace-Id). Visible to every member. */
  @Get()
  @RequirePermission('contacts.view')
  list(@CurrentUser() user: AuthUser, @CurrentTenant() tenant: TenantInfo): Promise<Member[]> {
    return this.members.list(user, tenant);
  }

  /** Change a member's role (owner/admin; owner-only for the owner role). Audited. */
  @Patch(':userId')
  @RequirePermission('team.manage')
  changeRole(
    @CurrentUser() user: AuthUser,
    @CurrentTenant() tenant: TenantInfo,
    @Param() params: UserIdParamDto,
    @Body() body: ChangeRoleDto,
    @Req() request: FastifyRequest,
  ): Promise<{ userId: string; role: string }> {
    return this.members.changeRole(user, tenant, params.userId, body.role, requestOrigin(request));
  }
}
