'use client';

import type { ResearchSpec } from '@leadforge/contracts';
import { X } from 'lucide-react';

import { Badge } from '@/components/ui/badge';

import type { Feasibility } from '../types';
import { GeographyAttribution } from './attribution';

/**
 * What we understood, in pieces the user can check and drop.
 *
 * Grouped the way docs/09 asks, and each chip carries how far we can actually take it: a filter a
 * maps search can apply is a different promise from one we can only check after enrichment, and
 * showing them the same way is how a user ends up believing a number we never measured.
 *
 * Chips can be removed but not rewritten here. Removing changes the spec — and so the estimate —
 * which is the edit that matters before spending; arbitrary editing needs the taxonomy and geo
 * endpoints that do not exist yet, so it is not offered rather than half-offered.
 */
export function SpecChips({
  spec,
  feasibility,
  onRemove,
}: {
  spec: ResearchSpec;
  feasibility: Feasibility[];
  onRemove: (group: ChipGroup, value: string) => void;
}) {
  const groups = chipGroups(spec);
  const showsGeography = groups.some((g) => g.key === 'location' && g.chips.length > 0);

  return (
    <div className="space-y-3">
      <dl className="space-y-3">
        {groups
          .filter((group) => group.chips.length > 0)
          .map((group) => (
            <div key={group.key} className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
              <dt className="text-muted-foreground w-20 shrink-0 text-xs">{group.label}</dt>
              <dd className="flex flex-1 flex-wrap gap-1.5">
                {group.chips.map((chip) => {
                  const reach = reachOf(chip, group.key, feasibility);
                  return (
                    <span
                      key={`${group.key}:${chip}`}
                      className="border-border bg-card inline-flex items-center gap-1.5 rounded-md border py-0.5 pr-1 pl-2 text-sm"
                    >
                      {chip}
                      {reach && (
                        <Badge variant={reach.variant} title={reach.title}>
                          {reach.short}
                        </Badge>
                      )}
                      {group.removable && (
                        <button
                          aria-label={`Remove ${chip}`}
                          className="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 rounded p-0.5 focus-visible:ring-2 focus-visible:outline-none"
                          onClick={() => {
                            onRemove(group.key, chip);
                          }}
                          type="button"
                        >
                          <X aria-hidden className="size-3" />
                        </button>
                      )}
                    </span>
                  );
                })}
              </dd>
            </div>
          ))}
      </dl>
      {showsGeography && <GeographyAttribution />}
    </div>
  );
}

export type ChipGroup = 'industry' | 'location' | 'fields' | 'keywords' | 'other';

interface Group {
  key: ChipGroup;
  label: string;
  chips: string[];
  removable: boolean;
}

export function chipGroups(spec: ResearchSpec): Group[] {
  const filters = spec.filters;
  const location = filters.location;
  const industry = filters.industry;
  const other: string[] = [];
  if (filters.has_website === true) other.push('has a website');
  if (filters.employee_count?.gte) other.push(`${filters.employee_count.gte}+ employees`);
  if (filters.rating?.gte) other.push(`rating ${filters.rating.gte}+`);
  if (filters.contact?.any_of?.length)
    other.push(`contact: ${filters.contact.any_of.join(' or ')}`);

  return [
    {
      key: 'industry',
      label: 'Industry',
      chips: (industry?.include ?? []).map(String),
      removable: true,
    },
    {
      key: 'location',
      label: 'Location',
      chips: [
        ...(location?.cities ?? []).map(String),
        ...(location?.states ?? []).map(String),
        ...(location?.country && !location.cities?.length && !location.states?.length
          ? [location.country]
          : []),
      ],
      removable: true,
    },
    {
      key: 'keywords',
      label: 'Keywords',
      chips: (spec.keywords?.must ?? []).map(String),
      removable: true,
    },
    { key: 'other', label: 'Filters', chips: other, removable: false },
    { key: 'fields', label: 'Columns', chips: spec.fields.map(String), removable: true },
  ];
}

/** How far a filter can actually be taken, in the user's words rather than the API's enum. */
function reachOf(
  chip: string,
  group: ChipGroup,
  feasibility: Feasibility[],
): { short: string; title: string; variant: 'success' | 'info' | 'warning' | 'danger' } | null {
  if (group === 'fields') return null;
  const match = feasibility.find(
    (f) => f.filter.toLowerCase() === chip.toLowerCase() || f.filter === group,
  );
  if (!match) return null;
  switch (match.mode) {
    case 'directly_searchable':
      return {
        short: 'searchable',
        title: match.note ?? 'Searched for directly.',
        variant: 'success',
      };
    case 'post_filter':
      return {
        short: 'checked later',
        title: match.note ?? 'Applied after the data is collected, not in the search itself.',
        variant: 'info',
      };
    case 'estimate_only':
      return {
        short: 'estimated',
        title: match.note ?? 'We can only estimate this, not confirm it.',
        variant: 'warning',
      };
    default:
      return {
        short: 'not supported',
        title: match.note ?? 'Nothing we have can check this yet.',
        variant: 'danger',
      };
  }
}
