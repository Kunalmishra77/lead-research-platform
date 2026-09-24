'use client';

import type { ResearchSpec } from '@leadforge/contracts';
import { Loader2, Sparkles } from 'lucide-react';
import { useActionState, useId, useMemo, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';

import { parseResearch, type ParseState, runResearch, type RunState } from '../actions';
import type { Depth, ParseResult } from '../types';
import { type ChipGroup, SpecChips } from './spec-chips';

const EXAMPLES = [
  'Dental clinics in Delhi with a website',
  'Restaurants in Pune with a phone number',
  'Chartered accountants in Ahmedabad',
];

const DEPTHS: { value: Depth; label: string; blurb: string }[] = [
  { value: 'quick', label: 'Quick', blurb: 'Name, address, phone, website.' },
  { value: 'standard', label: 'Standard', blurb: 'Adds the website profile and socials.' },
  { value: 'deep', label: 'Deep', blurb: 'Adds people, hiring and verification.' },
];

/**
 * Describe → review → run.
 *
 * The rule the screen is built around is docs/09's: cost before commit. Nothing is charged for
 * the parse, so the user gets to see what we understood and what it will cost before anything
 * spends. The Run button carries the number.
 */
export function NewResearch({ canRun }: { canRun: boolean }) {
  const [parseState, parse, parsing] = useActionState<ParseState, FormData>(parseResearch, {});
  const [runState, run, running] = useActionState<RunState, FormData>(runResearch, {});
  const promptId = useId();
  const [prompt, setPrompt] = useState('');
  const [spec, setSpec] = useState<ResearchSpec | null>(null);
  const [seen, setSeen] = useState<ParseResult | undefined>(undefined);

  // A fresh parse replaces whatever the user had been editing. Adjusted during render rather
  // than in an effect: an effect renders once with the old spec beside the new estimate, which
  // is a price shown against something else.
  const result = parseState.result;
  if (result !== seen) {
    setSeen(result);
    setSpec(result?.spec ?? null);
  }
  const estimate = useEstimate(result, spec);

  return (
    <div className="space-y-8">
      <form action={parse} className="space-y-3">
        <label className="text-sm font-medium" htmlFor={promptId}>
          What leads do you need?
        </label>
        <Textarea
          aria-describedby={parseState.fieldErrors?.rawQuery ? `${promptId}-error` : undefined}
          aria-invalid={Boolean(parseState.fieldErrors?.rawQuery)}
          id={promptId}
          name="rawQuery"
          onChange={(e) => {
            setPrompt(e.target.value);
          }}
          placeholder="Dental clinics in Delhi with a website"
          value={prompt}
        />
        {parseState.fieldErrors?.rawQuery && (
          <p className="text-danger text-sm" id={`${promptId}-error`} role="alert">
            {parseState.fieldErrors.rawQuery[0]}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Button disabled={parsing} type="submit">
            {parsing ? (
              <Loader2 aria-hidden className="size-4 animate-spin" />
            ) : (
              <Sparkles aria-hidden className="size-4" />
            )}
            {parsing ? 'Reading…' : 'Understand this'}
          </Button>
          <span className="text-muted-foreground text-xs">
            Free — nothing is charged to read it.
          </span>
        </div>
        <ul className="flex flex-wrap gap-2">
          {EXAMPLES.map((example) => (
            <li key={example}>
              <button
                className="border-border text-muted-foreground hover:text-foreground hover:border-foreground/30 rounded-md border px-2 py-1 text-xs"
                onClick={() => {
                  setPrompt(example);
                }}
                type="button"
              >
                {example}
              </button>
            </li>
          ))}
        </ul>
      </form>

      {parseState.error && (
        <p
          className="border-danger/30 bg-danger/5 text-danger rounded-lg border p-3 text-sm"
          role="alert"
        >
          {parseState.error}
        </p>
      )}

      {result && spec && (
        <section aria-labelledby="understood" className="space-y-6">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-base font-medium" id="understood">
              Here is what we understood
            </h2>
            <span className="text-muted-foreground text-xs">
              {result.provenance.cached ? 'From a recent identical request' : 'Read just now'}
            </span>
          </div>

          {result.needsConfirmation && (
            <p
              className="border-warning/30 bg-warning/5 rounded-lg border p-3 text-sm"
              role="status"
            >
              This one is open to interpretation, so it is worth a look before you spend anything.
              {result.ambiguities.length > 0 && ` ${result.ambiguities.join(' ')}`}
            </p>
          )}

          <SpecChips
            feasibility={result.feasibility}
            onRemove={(group, value) => {
              setSpec((current) => removeChip(current, group, value));
            }}
            spec={spec}
          />

          {result.unsupported.length > 0 && (
            <p className="text-muted-foreground text-sm">
              We cannot check {result.unsupported.join(', ')} — it will not be part of the search.
            </p>
          )}

          <DepthPicker
            onChange={(depth) => {
              setSpec((current) => (current ? { ...current, depth } : current));
            }}
            value={spec.depth}
          />

          <RunBar
            action={run}
            canRun={canRun}
            estimate={estimate}
            rawQuery={parseState.rawQuery ?? prompt}
            running={running}
            runState={runState}
            spec={spec}
          />
        </section>
      )}
    </div>
  );
}

function DepthPicker({ value, onChange }: { value: Depth; onChange: (d: Depth) => void }) {
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">How deep should it go?</legend>
      <div className="grid gap-2 sm:grid-cols-3">
        {DEPTHS.map((depth) => (
          <label
            className={`cursor-pointer rounded-lg border p-3 text-sm ${
              value === depth.value
                ? 'border-primary bg-primary/5'
                : 'border-border hover:border-foreground/30'
            }`}
            key={depth.value}
          >
            <input
              checked={value === depth.value}
              className="sr-only"
              name="depth"
              onChange={() => {
                onChange(depth.value);
              }}
              type="radio"
              value={depth.value}
            />
            <span className="block font-medium">{depth.label}</span>
            <span className="text-muted-foreground block text-xs">{depth.blurb}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function RunBar({
  action,
  spec,
  rawQuery,
  estimate,
  canRun,
  running,
  runState,
}: {
  action: (form: FormData) => void;
  spec: ResearchSpec;
  rawQuery: string;
  estimate: { credits: number; perLead: number; maxResults: number };
  canRun: boolean;
  running: boolean;
  runState: RunState;
}) {
  // One key per reviewed request: a double submit must not start -- or reserve credits for -- two
  // jobs, and the same-origin proxy cannot carry an Idempotency-Key, so the Server Action sets it.
  const idempotencyKey = useMemo(
    () => `web:${hash(JSON.stringify({ rawQuery, spec }))}`,
    [rawQuery, spec],
  );

  return (
    <form action={action} className="space-y-3">
      <input name="rawQuery" type="hidden" value={rawQuery} />
      <input name="spec" type="hidden" value={JSON.stringify(spec)} />
      <input name="idempotencyKey" type="hidden" value={idempotencyKey} />

      <div className="border-border bg-card flex flex-wrap items-center justify-between gap-3 rounded-lg border p-4">
        <div className="space-y-0.5">
          <p className="text-sm">
            Up to{' '}
            <strong className="tabular-nums">{estimate.maxResults.toLocaleString('en-IN')}</strong>{' '}
            leads, at <strong className="tabular-nums">{estimate.perLead}</strong> credits each.
          </p>
          <p className="text-muted-foreground text-xs">
            Up to <span className="tabular-nums">{estimate.credits.toLocaleString('en-IN')}</span>{' '}
            credits are held while it runs. Anything unused comes back.
          </p>
        </div>
        <Button disabled={!canRun || running} size="lg" type="submit">
          {running && <Loader2 aria-hidden className="size-4 animate-spin" />}
          {running ? 'Starting…' : `Run · ${estimate.credits.toLocaleString('en-IN')} credits`}
        </Button>
      </div>

      {!canRun && (
        <p className="text-muted-foreground text-sm" role="status">
          Your role can view research but not start it. Ask an admin to run this one.
        </p>
      )}
      {runState.error && (
        <p
          className="border-danger/30 bg-danger/5 text-danger rounded-lg border p-3 text-sm"
          role="alert"
        >
          {runState.error}
          {runState.needsCredits && ' Top up to run it.'}
        </p>
      )}
    </form>
  );
}

/**
 * What this run will hold, recomputed locally as the user edits.
 *
 * The API's estimate is the one that counts and it is recomputed server-side when the job is
 * created; this only keeps the number on the button honest while chips and depth change, so the
 * figure never contradicts what is about to happen.
 */
function useEstimate(result: ParseResult | undefined, spec: ResearchSpec | null) {
  return useMemo(() => {
    const perLead = perLeadFor(spec?.depth ?? result?.estimate.depth ?? 'standard', result);
    const maxResults = spec?.limits.max_results ?? result?.estimate.maxResults ?? 0;
    const maxCredits = spec?.limits.max_credits ?? Number.POSITIVE_INFINITY;
    return { perLead, maxResults, credits: Math.min(perLead * maxResults, maxCredits) };
  }, [result, spec]);
}

/** The rate the API quoted for the depth it quoted; the others from the published ratio. */
function perLeadFor(depth: Depth, result: ParseResult | undefined): number {
  const quoted = result?.estimate;
  if (quoted && quoted.depth === depth) return quoted.creditsPerLead;
  return { quick: 1, standard: 3, deep: 8 }[depth];
}

function removeChip(
  spec: ResearchSpec | null,
  group: ChipGroup,
  value: string,
): ResearchSpec | null {
  if (!spec) return spec;
  const without = <T,>(list: T[] | undefined) => (list ?? []).filter((v) => String(v) !== value);
  switch (group) {
    case 'industry':
      return {
        ...spec,
        filters: {
          ...spec.filters,
          industry: { ...spec.filters.industry, include: without(spec.filters.industry?.include) },
        },
      };
    case 'location':
      return {
        ...spec,
        filters: {
          ...spec.filters,
          location: {
            ...spec.filters.location,
            cities: without(spec.filters.location?.cities),
            states: without(spec.filters.location?.states),
          },
        },
      };
    case 'keywords':
      return { ...spec, keywords: { ...spec.keywords, must: without(spec.keywords?.must) } };
    case 'fields': {
      const fields = without(spec.fields);
      // The contract needs at least one column, and a run with none would return rows of nothing.
      return fields.length > 0 ? { ...spec, fields } : spec;
    }
    default:
      return spec;
  }
}

/** Small, stable, and only ever used to key an idempotent submit. */
function hash(input: string): string {
  let h = 2166136261;
  for (let i = 0; i < input.length; i += 1) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(36);
}
