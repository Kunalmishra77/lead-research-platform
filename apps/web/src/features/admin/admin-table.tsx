import Link from 'next/link';
import type { ReactNode } from 'react';

import { Button } from '@/components/ui/button';

export interface Column<T> {
  header: string;
  cell: (row: T) => ReactNode;
  numeric?: boolean;
}

/** Read-only table with cursor pagination ("First page" / "Next page"). */
export function AdminTable<T extends { id: string }>({
  caption,
  columns,
  rows,
  nextHref,
  firstHref,
}: {
  caption: string;
  columns: Column<T>[];
  rows: T[];
  nextHref: string | null;
  firstHref: string | null;
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <caption className="sr-only">{caption}</caption>
          <thead className="bg-muted/50 text-left text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              {columns.map((c) => (
                <th
                  key={c.header}
                  scope="col"
                  className={c.numeric ? 'px-3 py-2 text-right' : 'px-3 py-2'}
                >
                  {c.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length}
                  className="px-3 py-8 text-center text-muted-foreground"
                >
                  Nothing here yet.
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={row.id} className="border-t">
                  {columns.map((c) => (
                    <td
                      key={c.header}
                      className={c.numeric ? 'px-3 py-2 text-right tabular-nums' : 'px-3 py-2'}
                    >
                      {c.cell(row)}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <div className="flex justify-end gap-2">
        {firstHref ? (
          <Button asChild variant="outline" size="sm">
            <Link href={firstHref}>First page</Link>
          </Button>
        ) : null}
        {nextHref ? (
          <Button asChild variant="outline" size="sm">
            <Link href={nextHref}>Next page</Link>
          </Button>
        ) : null}
      </div>
    </div>
  );
}
