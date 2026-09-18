import { Body, Controller, Get, HttpCode, Post } from '@nestjs/common';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';

import { CurrentUser } from '../../common/current-request.decorator';
import type { AuthUser } from '../auth/auth.types';
import { type CreatedOrg, CreateOrgDto, type MeResponse } from './orgs.dto';
import { OrgsService } from './orgs.service';

@ApiTags('account')
@ApiBearerAuth()
@Controller('app')
export class OrgsController {
  constructor(private readonly orgs: OrgsService) {}

  /** Current user, their memberships (org + workspace + role). */
  @Get('me')
  me(@CurrentUser() user: AuthUser): Promise<MeResponse> {
    return this.orgs.me(user);
  }

  /** Create an organization with a default workspace; the caller becomes owner. */
  @Post('orgs')
  @HttpCode(201)
  create(@CurrentUser() user: AuthUser, @Body() body: CreateOrgDto): Promise<CreatedOrg> {
    return this.orgs.create(user, body.name, body.slug);
  }
}
