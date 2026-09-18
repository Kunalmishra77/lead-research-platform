import { createZodDto } from 'nestjs-zod';
import { z } from 'zod';

export const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{1,46}[a-z0-9]$/;

export const CreateOrgSchema = z.object({
  name: z.string().trim().min(1).max(120),
  slug: z
    .string()
    .regex(SLUG_PATTERN, 'lowercase letters, digits and dashes (3-48 characters)')
    .optional(),
});
export class CreateOrgDto extends createZodDto(CreateOrgSchema) {}

export interface CreatedOrg {
  org: { id: string; name: string; slug: string };
  workspace: { id: string; name: string };
  role: 'owner';
}

export interface MeResponse {
  user: { id: string; email: string | null; fullName: string | null; isPlatformStaff: boolean };
  memberships: {
    orgId: string;
    orgName: string;
    orgSlug: string;
    workspaceId: string;
    workspaceName: string;
    role: string;
  }[];
}
