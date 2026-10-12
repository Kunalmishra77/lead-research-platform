import type { LeadView } from '../types';

/**
 * Says so when a lead's details have been pruned, rather than letting the row look broken.
 *
 * A lead's values are deleted a while after it is exported, because the free database holds
 * about 27,000 leads and the product's job is to deliver them rather than store them
 * (ADR-0016). What survives is the business: its name, address and phone, which live in the
 * company graph rather than in `field_values`.
 *
 * Without this line the row reads as a lead we failed to enrich -- every column a dash, no
 * reason given -- which is both wrong and the kind of wrong that gets reported as a bug.
 */
export function PrunedNote({ lead }: { lead: LeadView }) {
  // Exported and still carrying its values is the normal case inside the grace window; there is
  // nothing missing to explain.
  if (lead.exportedAt === null || lead.values.length > 0) return null;
  return (
    <span className="text-muted-foreground block text-xs">
      Details cleared after export on{' '}
      {new Date(lead.exportedAt).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })}
      {' · '}
      <span className="whitespace-nowrap">they are in your downloaded file</span>
    </span>
  );
}
