import { createZodDto } from 'nestjs-zod';
import { z } from 'zod';

export const AdminListQuerySchema = z.object({
  limit: z.coerce.number().int().min(1).max(200).default(50),
  /** `nextCursor` of the previous page (docs/05 pagination convention). */
  cursor: z.uuid().optional(),
});
export class AdminListQueryDto extends createZodDto(AdminListQuerySchema) {}

export interface Page<T> {
  items: T[];
  /** Pass as `cursor` for the next page; null on the last page. */
  nextCursor: string | null;
}

export interface AdminOrg {
  id: string;
  name: string;
  slug: string;
  plan: string;
  region: string;
  createdAt: string;
  memberCount: number;
}

export interface AdminUser {
  id: string;
  email: string | null;
  createdAt: string | null;
  emailConfirmedAt: string | null;
  lastSignInAt: string | null;
  isPlatformStaff: boolean;
  orgCount: number;
}
