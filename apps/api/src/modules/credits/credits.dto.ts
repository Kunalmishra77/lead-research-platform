import { z } from 'zod';

export const RESEARCH_DEPTHS = ['quick', 'standard', 'deep'] as const;
export const researchDepthSchema = z.enum(RESEARCH_DEPTHS);
export type ResearchDepth = (typeof RESEARCH_DEPTHS)[number];

/** Credit summary returned with a research job (docs/09 shows cost before and during a run). */
export interface CreditsSummary {
  reserved: number;
  used: number;
  balance: number;
}
