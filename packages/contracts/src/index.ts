export type { Budget, JobEnvelope } from './generated/job-envelope.js';
export type { ProgressEvent } from './generated/progress-event.js';
export type { ResearchSpec } from './generated/research-spec.js';
export { jobEnvelopeSchema, progressEventSchema, researchSpecSchema } from './generated/schemas.js';
export {
  validateJobEnvelope,
  validateProgressEvent,
  validateResearchSpec,
  type ValidationResult,
} from './validate.js';

export const ENVELOPE_VERSION = 1;
export const PROGRESS_EVENT_VERSION = 1;
export const RESEARCH_SPEC_VERSION = 1;
