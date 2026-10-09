import Link from 'next/link';

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

import type { LeadsPage, LeadView } from '../types';
import { valueOf } from '../types';
import { SourceAttribution } from './attribution';
import { LeadDetails } from './lead-details';
import { ProvenanceLegend, ValueCell } from './provenance';
import { SocialsCell } from './socials-cell';

/**
 * The leads a job delivered.
 *
 * Every cell carries where its value came from (docs/09, CLAUDE.md) — not as decoration but
 * because a phone number found on a map and one a rule guessed are different things to act on.
 * The attribution underneath is a condition of using the data at all, so it renders from the
 * sources this page actually contains rather than being assumed.
 */
/**
 * What an empty grid means, which is three different things.
 *
 * The first version of this said "found no businesses matching your description" for every
 * finished job, and printed it directly under a counter reading "Businesses seen 12". A screen
 * that contradicts itself one line later is worse than the vague message it replaced.
 */
export function emptyMessage({
  finished,
  candidates,
}: {
  finished: boolean;
  candidates: number | undefined;
}): string {
  if (!finished) return 'No leads yet. They appear here as the search finds them.';
  if (!candidates) {
    return 'This search found no businesses matching your description. Try widening the area, or asking for a nearby category as well.';
  }
  return `This run found ${candidates.toLocaleString('en-IN')} businesses but delivered none to this workspace.`;
}

export function ResultsTable({
  page,
  jobId,
  finished = false,
  candidates,
}: {
  page: LeadsPage;
  jobId: string;
  /** Whether the job has stopped. An empty grid means different things before and after. */
  finished?: boolean;
  /** How many businesses the run saw, so an empty grid cannot contradict the counter above it. */
  candidates?: number;
}) {
  if (page.items.length === 0) {
    return (
      <p className="border-border text-muted-foreground rounded-lg border border-dashed p-6 text-center text-sm">
        {emptyMessage({ finished, candidates })}
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="border-border overflow-hidden rounded-lg border">
        <Table>
          <caption className="sr-only">Leads delivered by this research job</caption>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">Business</TableHead>
              <TableHead scope="col">City</TableHead>
              <TableHead scope="col">Email</TableHead>
              <TableHead scope="col">Phone</TableHead>
              <TableHead scope="col">Website</TableHead>
              <TableHead scope="col">Profiles</TableHead>
              <TableHead className="text-right" scope="col">
                Rating
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {page.items.map((lead) => {
              const website = valueOf(lead, 'website');
              return (
                <TableRow key={lead.id}>
                  {/* The name opens the drawer: the grid shows what a person scans, and the
                      rest -- including the full provenance of every value -- lives one click away
                      rather than in a row nobody can read. */}
                  <TableCell className="align-top">
                    <LeadDetails lead={lead} />
                  </TableCell>
                  <TableCell className="align-top">
                    <ValueCell value={valueOf(lead, 'city')} />
                  </TableCell>
                  {/* The column this phase was for. A maps listing never carries an email; this
                      one came off the company's own contact page. */}
                  <TableCell className="max-w-56 truncate align-top">
                    <EmailCell lead={lead} />
                  </TableCell>
                  <TableCell className="align-top tabular-nums">
                    <ValueCell value={valueOf(lead, 'phone')} />
                  </TableCell>
                  <TableCell className="max-w-56 truncate align-top">
                    {website ? (
                      <a
                        className="hover:text-foreground underline underline-offset-2"
                        href={String(website.value)}
                        rel="noreferrer noopener nofollow"
                        target="_blank"
                      >
                        <ValueCell value={website} />
                      </a>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell className="align-top">
                    <SocialsCell lead={lead} />
                  </TableCell>
                  <TableCell className="align-top text-right tabular-nums">
                    <ValueCell value={valueOf(lead, 'rating')} />
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <ProvenanceLegend />
        {page.nextCursor && (
          <Link
            className="text-muted-foreground hover:text-foreground text-sm underline underline-offset-2"
            href={`/research/${jobId}?cursor=${page.nextCursor}`}
          >
            Next page
          </Link>
        )}
      </div>

      <SourceAttribution geography sources={page.sources} />
    </div>
  );
}

/**
 * The email, as a link to compose one.
 *
 * `mailto:` because the next thing anyone does with an address is write to it, and the dot stays
 * on the value: an address linked from a contact page and one decoded out of `info [at] clinic`
 * are both found, but a reader can still see which page each came from in the drawer.
 */
function EmailCell({ lead }: { lead: LeadView }) {
  const email = valueOf(lead, 'email');
  if (!email) return <span className="text-muted-foreground">—</span>;
  return (
    <a
      className="hover:text-foreground underline underline-offset-2"
      href={`mailto:${String(email.value)}`}
    >
      <ValueCell value={email} />
    </a>
  );
}
