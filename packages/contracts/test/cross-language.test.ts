import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import {
  validateJobEnvelope,
  validateProgressEvent,
  validateResearchSpec,
  type ValidationResult,
} from '../src/index.js';

const root = resolve(import.meta.dirname, '..');

const validators: Record<string, (data: unknown) => ValidationResult<unknown>> = {
  'job-envelope': validateJobEnvelope,
  'progress-event': validateProgressEvent,
  'research-spec': validateResearchSpec,
};

interface Row {
  contract: string;
  name: string;
  wire: unknown;
}

function pythonRoundTrip(): Row[] {
  const out = execFileSync(
    'uv',
    ['run', '--project', root, '--frozen', 'python', join(root, 'tests_py', 'roundtrip.py')],
    { encoding: 'utf8' },
  );
  return out
    .split('\n')
    .filter((line) => line.trim() !== '')
    .map((line) => JSON.parse(line) as Row);
}

describe('Python to_wire() output', () => {
  const rows = pythonRoundTrip();

  it('covers every valid fixture', () => {
    expect(rows.length).toBeGreaterThanOrEqual(7);
  });

  it.each(rows.map((r) => [`${r.contract}/${r.name}`, r] as const))(
    '%s passes the TS validator and equals the fixture',
    (_label, row) => {
      const validate = validators[row.contract];
      if (!validate) throw new Error(`unknown contract ${row.contract}`);
      const result = validate(row.wire);
      expect(result.ok ? [] : result.errors).toEqual([]);
      const fixture: unknown = JSON.parse(
        readFileSync(join(root, 'fixtures', row.contract, 'valid', row.name), 'utf8'),
      );
      expect(row.wire).toEqual(fixture);
    },
  );
});
