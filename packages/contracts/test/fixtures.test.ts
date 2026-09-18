import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import {
  validateJobEnvelope,
  validateProgressEvent,
  validateResearchSpec,
  type ValidationResult,
} from '../src/index.js';

const fixturesDir = join(import.meta.dirname, '..', 'fixtures');

const validators: Record<string, (data: unknown) => ValidationResult<unknown>> = {
  'job-envelope': validateJobEnvelope,
  'progress-event': validateProgressEvent,
  'research-spec': validateResearchSpec,
};

function cases(contract: string, kind: 'valid' | 'invalid'): [string, unknown][] {
  const dir = join(fixturesDir, contract, kind);
  return readdirSync(dir).map((f) => [
    f,
    JSON.parse(readFileSync(join(dir, f), 'utf8')) as unknown,
  ]);
}

describe.each(Object.entries(validators))('%s', (contract, validate) => {
  it.each(cases(contract, 'valid'))('accepts valid/%s', (_name, data) => {
    const result = validate(data);
    expect(result.ok ? [] : result.errors).toEqual([]);
  });

  it.each(cases(contract, 'invalid'))('rejects invalid/%s', (_name, data) => {
    expect(validate(data).ok).toBe(false);
  });
});

describe('error messages', () => {
  it('point at the failing field', () => {
    const result = validateJobEnvelope({
      ...(cases('job-envelope', 'valid')[0]?.[1] as object),
      attempt: 0,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errors.join('\n')).toContain('/attempt');
  });
});
