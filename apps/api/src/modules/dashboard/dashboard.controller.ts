import { Controller, Get } from '@nestjs/common';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';

import { CurrentTenant, CurrentUser } from '../../common/current-request.decorator';
import { RequirePermission } from '../../common/rbac/require-permission.decorator';
import type { AuthUser, TenantInfo } from '../auth/auth.types';
import type { DashboardView } from './dashboard.dto';
import { DashboardService } from './dashboard.service';

/** The overview screen (docs/09). Reading it spends nothing, so a viewer may see it. */
@ApiTags('dashboard')
@ApiBearerAuth()
@Controller('app/dashboard')
export class DashboardController {
  constructor(private readonly dashboard: DashboardService) {}

  @Get()
  @RequirePermission('contacts.view')
  view(@CurrentUser() user: AuthUser, @CurrentTenant() tenant: TenantInfo): Promise<DashboardView> {
    return this.dashboard.view(user, tenant);
  }
}
