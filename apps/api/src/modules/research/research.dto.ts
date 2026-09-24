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

export const LeadsQuerySchema = z.object({
  limit: z.coerce.number().int().min(1).max(200).default(50),
  cursor: z.uuid().optional(),
});
export class LeadsQueryDto extends createZodDto(LeadsQuerySchema) {}

/**
 * One observed value and where it came from. Every field the UI renders carries this, because a
 * value without its provenance is not something we are willing to show (CLAUDE.md).
 */
export interface LeadValue {
  field: string;
  value: unknown;
  /** `app.sources.key`, e.g. `google_places`. What attribution keys on (ADR-0011, docs/09). */
  source: string;
  sourceUrl: string;
  /** When the source showed this, not when we wrote it down. */
  observedAt: string;
  method: string;
  /** found | derived_pattern | provider | user — the badge the UI draws (docs/09). */
  derivation: string | null;
  confidence: number;
}

export interface LeadView {
  id: string;
  companyId: string;
  status: string;
  createdAt: string;
  name: string;
  domain: string | null;
  city: string | null;
  country: string | null;
  address: string | null;
  phone: string | null;
  googlePlaceId: string | null;
  values: LeadValue[];
}

export interface LeadsPage {
  items: LeadView[];
  nextCursor: string | null;
  /**
   * Every source that contributed a value on this page. The UI shows one attribution block per
   * source rather than repeating it on each cell — Google's terms require it visible, not loud.
   */
  sources: string[];
}
