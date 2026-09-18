import { applyDecorators, SetMetadata, UseGuards } from '@nestjs/common';
import { ApiHeader } from '@nestjs/swagger';

import type { Permission } from './permissions';
import { REQUIRED_PERMISSION, TenantGuard, WORKSPACE_HEADER } from './tenant.guard';

/**
 * Route needs an active workspace (X-Workspace-Id) and `permission` for the caller's role there.
 * Handlers then read `request.tenant` and run queries inside withTenant().
 */
export function RequirePermission(permission: Permission): MethodDecorator & ClassDecorator {
  return applyDecorators(
    SetMetadata(REQUIRED_PERMISSION, permission),
    UseGuards(TenantGuard),
    ApiHeader({ name: WORKSPACE_HEADER, required: true, description: 'Active workspace id' }),
  );
}
