import { createZodDto } from 'nestjs-zod';
import { z } from 'zod';

import { MEMBERSHIP_ROLES } from '../../common/rbac/permissions';

export const ChangeRoleSchema = z.object({ role: z.enum(MEMBERSHIP_ROLES) });
export class ChangeRoleDto extends createZodDto(ChangeRoleSchema) {}

export const UserIdParamSchema = z.object({ userId: z.uuid() });
export class UserIdParamDto extends createZodDto(UserIdParamSchema) {}

export interface Member {
  userId: string;
  email: string | null;
  fullName: string | null;
  role: string;
  joinedAt: string;
}
