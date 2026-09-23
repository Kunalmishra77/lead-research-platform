import { Ajv2020, type ErrorObject } from 'ajv/dist/2020.js';
import ajvFormats from 'ajv-formats';

import type { JobEnvelope } from './generated/job-envelope.js';
import type { ProgressEvent } from './generated/progress-event.js';
import type { ResearchParseReply } from './generated/research-parse-reply.js';
import type { ResearchSpec } from './generated/research-spec.js';
import {
  jobEnvelopeSchema,
  progressEventSchema,
  researchParseReplySchema,
  researchSpecSchema,
} from './generated/schemas.js';

export type ValidationResult<T> = { ok: true; value: T } | { ok: false; errors: string[] };

const ajv = new Ajv2020({ allErrors: true, strict: true });
// ajv-formats is CommonJS: the default import is module.exports, whose `.default` is the plugin.
ajvFormats.default(ajv);

function describe(errors: ErrorObject[] | null | undefined): string[] {
  return (errors ?? []).map((e) => `${e.instancePath || '/'} ${e.message ?? 'is invalid'}`);
}

function validator<T>(schema: object): (data: unknown) => ValidationResult<T> {
  const check = ajv.compile<T>(schema);
  return (data) =>
    check(data) ? { ok: true, value: data } : { ok: false, errors: describe(check.errors) };
}

export const validateJobEnvelope = validator<JobEnvelope>(jobEnvelopeSchema);
export const validateProgressEvent = validator<ProgressEvent>(progressEventSchema);
export const validateResearchSpec = validator<ResearchSpec>(researchSpecSchema);
export const validateResearchParseReply = validator<ResearchParseReply>(researchParseReplySchema);
