import Link from 'next/link';

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

import type { LeadsPage } from '../types';
import { valueOf } from '../types';
import { SourceAttribution } from './attribution';
import { ProvenanceLegend, ValueCell } from './provenance';

/**
 * The leads a job delivered.
 *
 * Every cell carries where its value came from (docs/09, CLAUDE.md) — not as decoration but
 * because a phone number found on a map and one a rule guessed are different things to act on.
 * The attribution underneath is a condition of using the data at all, so it renders from the
 * sources this page actually contains rather than being assumed.
 */
export function ResultsTable({ page, jobId }: { page: LeadsPage; jobId: string }) {
  if (page.items.length === 0) {
    return (
      <p className="border-border text-muted-foreground rounded-lg border border-dashed p-6 text-center text-sm">
        No leads yet. They appear here as the search finds them.
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
              <TableHead scope="col">Phone</TableHead>
              <TableHead scope="col">Website</TableHead>
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
                  <TableCell className="font-medium">
                    <ValueCell value={valueOf(lead, 'name')} />
                  </TableCell>
                  <TableCell>
                    <ValueCell value={valueOf(lead, 'city')} />
                  </TableCell>
                  <TableCell className="tabular-nums">
                    <ValueCell value={valueOf(lead, 'phone')} />
                  </TableCell>
                  <TableCell className="max-w-56 truncate">
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
                  <TableCell className="text-right tabular-nums">
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
