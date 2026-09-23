export type { Budget, JobEnvelope } from './generated/job-envelope.js';
export type { ProgressEvent } from './generated/progress-event.js';
export type {
  ParseError,
  ParseFeasibility,
  ParseProvenance,
  ParseResult,
  ResearchParseReply,
} from './generated/research-parse-reply.js';
export type { ResearchSpec } from './generated/research-spec.js';
export {
  jobEnvelopeSchema,
  progressEventSchema,
  researchParseReplySchema,
  researchSpecSchema,
} from './generated/schemas.js';
export {
  validateJobEnvelope,
  validateProgressEvent,
  validateResearchParseReply,
  validateResearchSpec,
  type ValidationResult,
} from './validate.js';

export const ENVELOPE_VERSION = 1;
export const PROGRESS_EVENT_VERSION = 1;
export const RESEARCH_SPEC_VERSION = 1;
export const RESEARCH_PARSE_REPLY_VERSION = 1;
