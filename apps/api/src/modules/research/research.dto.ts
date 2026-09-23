import { type ResearchSpec, validateResearchSpec } from '@leadforge/contracts';
import { createZodDto } from 'nestjs-zod';
import { z } from 'zod';

/** The spec is owned by packages/contracts; Zod only hands it to that validator (one source). */
const specSchema = z
  .unknown()
  .superRefine((value, ctx) => {
    const result = validateResearchSpec(value);
    if (!result.ok) {
      ctx.addIssue({
        code: 'custom',
        message: `invalid ResearchSpec: ${result.errors.join('; ')}`,
      });
    }
  })
  .transform((value) => value as ResearchSpec);

export const CreateResearchSchema = z.object({
  /** What the user typed; the spec is what we run (docs/06 section 1). */
  rawQuery: z.string().trim().min(1).max(2000),
  spec: specSchema,
});
export class CreateResearchDto extends createZodDto(CreateResearchSchema) {}

export const ParseResearchSchema = z.object({
  /** What the user typed. The spec comes back from the parse; nothing is charged for it. */
  rawQuery: z.string().trim().min(1).max(2000),
});
export class ParseResearchDto extends createZodDto(ParseResearchSchema) {}

export const JobIdParamSchema = z.object({ id: z.uuid() });
export class JobIdParamDto extends createZodDto(JobIdParamSchema) {}

export const ResearchListQuerySchema = z.object({
  limit: z.coerce.number().int().min(1).max(200).default(50),
  cursor: z.uuid().optional(),
});
export class ResearchListQueryDto extends createZodDto(ResearchListQuerySchema) {}

export interface ResearchCredits {
  /** Held for this run; released when it ends (docs/11). */
  reserved: number;
  used: number;
  /** Workspace balance after the reservation. */
  balance: number;
}

export interface ResearchJobView {
  id: string;
  status: string;
  depth: string;
  rawQuery: string;
  spec: ResearchSpec;
  progress: unknown;
  credits: ResearchCredits;
  errorClass: string | null;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
}

export interface ResearchJobListItem {
  id: string;
  status: string;
  depth: string;
  rawQuery: string;
  creditsReserved: number;
  creditsUsed: number;
  createdAt: string;
  finishedAt: string | null;
}

export interface ResearchPage {
  items: ResearchJobListItem[];
  nextCursor: string | null;
}
