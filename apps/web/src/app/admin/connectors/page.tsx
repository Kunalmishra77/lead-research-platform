import type { Metadata } from 'next';

import { AdminTable, type Column } from '@/features/admin/admin-table';
import {
  type AdminConnector,
  type AdminCrawlHealth,
  type AdminMeter,
  connectorHealth,
  crawlHealth,
  usageByMeter,
} from '@/features/admin/api';
import {
  formatBytes,
  formatDateTime,
  formatDuration,
  formatMicros,
  formatShare,
} from '@/features/admin/format';

export const metadata: Metadata = { title: 'Connectors' };

/** AdminTable keys rows by `id`; these reads group by a name, which is already unique. */
type Keyed<T> = T & { id: string };

const connectorColumns: Column<Keyed<AdminConnector>>[] = [
  { header: 'Connector', cell: (c) => <span className="font-medium">{c.source}</span> },
  { header: 'Runs', cell: (c) => c.total, numeric: true },
  { header: 'Ok', cell: (c) => c.completed, numeric: true },
  {
    header: 'Failed',
    cell: (c) => (c.failed > 0 ? <span className="text-danger">{c.failed}</span> : 0),
    numeric: true,
  },
  { header: 'In flight', cell: (c) => c.running + c.queued, numeric: true },
  // These two are the reason the page exists: rate_limited means slow down, access_restricted
  // means stop and never retry (CLAUDE.md error taxonomy). A single "errors" count would hide it.
  { header: 'Rate limited', cell: (c) => c.rateLimited, numeric: true },
  {
    header: 'Restricted',
    cell: (c) =>
      c.accessRestricted > 0 ? <span className="text-danger">{c.accessRestricted}</span> : 0,
    numeric: true,
  },
  { header: 'Parse failed', cell: (c) => c.parseFailed, numeric: true },
  { header: 'Transient', cell: (c) => c.transient, numeric: true },
  { header: 'Budget out', cell: (c) => c.budgetExhausted, numeric: true },
  { header: 'Avg', cell: (c) => formatDuration(c.avgMs), numeric: true },
  { header: 'Last run', cell: (c) => formatDateTime(c.lastRunAt) },
];

const meterColumns: Column<Keyed<AdminMeter>>[] = [
  { header: 'Meter', cell: (m) => <span className="font-medium">{m.meter}</span> },
  { header: 'Events', cell: (m) => m.events, numeric: true },
  { header: 'Units', cell: (m) => m.units, numeric: true },
  { header: 'Credits', cell: (m) => m.credits, numeric: true },
  { header: 'Cost', cell: (m) => formatMicros(m.costMicros), numeric: true },
  { header: 'Orgs', cell: (m) => m.orgs, numeric: true },
  { header: 'Last event', cell: (m) => formatDateTime(m.lastEventAt) },
];

/**
 * One number with its label. A stat row rather than a table, because `admin_crawl_health`
 * returns a single row and a one-row table reads as a bug.
 */
function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border p-3">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-lg font-semibold tabular-nums">{value}</span>
      {note ? <span className="text-xs text-muted-foreground">{note}</span> : null}
    </div>
  );
}

function CrawlHealth({ crawl }: { crawl: AdminCrawlHealth }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      <Stat
        label="Crawl tasks"
        value={String(crawl.tasks)}
        note={`${crawl.completed} done, ${crawl.failed} failed`}
      />
      <Stat
        label="Pages read"
        value={String(crawl.pagesRead)}
        note={`${formatShare(crawl.pagesRead, crawl.tasks)} of tasks got a page`}
      />
      {/* The number that explains the email rate: most crawls never get a page at all. */}
      <Stat
        label="Got no page"
        value={String(crawl.tasksWithNoPage)}
        note="robots, dead host, or turned away"
      />
      <Stat label="Turned away" value={String(crawl.restricted)} note="an outcome, never retried" />
      <Stat label="Values written" value={String(crawl.valuesWritten)} />
      <Stat
        label="Pages kept"
        value={String(crawl.bodiesStored)}
        note={`${formatBytes(crawl.bytesStored)} on disk, ${crawl.deduplicated} already had`}
      />
      <Stat
        label="Time per crawl"
        value={formatDuration(crawl.avgMs)}
        note={`p95 ${formatDuration(crawl.p95Ms)}`}
      />
      {/* Named for what it measures: the crawl calls no paid API, so this is the whole
          pipeline's spend over the leads it delivered (migration 0029). */}
      <Stat
        label="Pipeline cost per lead"
        value={
          crawl.pipelineMicrosPerLead === null ? '—' : formatMicros(crawl.pipelineMicrosPerLead)
        }
        note={`${formatMicros(crawl.pipelineCostMicros)} over ${crawl.leadsDelivered} leads`}
      />
    </div>
  );
}

export default async function AdminConnectorsPage() {
  const [connectors, meters, crawl] = await Promise.all([
    connectorHealth(),
    usageByMeter(),
    crawlHealth(),
  ]);
  const spend = meters.reduce((total, m) => total + m.costMicros, 0);

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Connectors</h1>
          <p className="text-sm text-muted-foreground">
            Last 7 days. Counted from task outcomes, not from a counter each connector keeps — a
            connector that dies before writing its own metric would look healthy.
          </p>
        </div>
        <AdminTable
          caption="Connector health"
          columns={connectorColumns}
          rows={connectors.map((c) => ({ ...c, id: c.source }))}
          nextHref={null}
          firstHref={null}
        />
      </div>

      {crawl ? (
        <div className="flex flex-col gap-4">
          <div>
            <h2 className="text-xl font-semibold tracking-tight">Crawl</h2>
            <p className="text-sm text-muted-foreground">
              Since {formatDateTime(crawl.since)}. Last crawl {formatDateTime(crawl.lastCrawlAt)}.
              Read from the rows the pipeline already writes — a counter kept in a worker would be
              lost on every deploy, and this deploys several times a day.
            </p>
          </div>
          <CrawlHealth crawl={crawl} />
        </div>
      ) : null}

      <div className="flex flex-col gap-4">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Billed usage</h2>
          <p className="text-sm text-muted-foreground">
            From <code>usage_events</code>, which every paid API, model and browser call goes
            through. {formatMicros(spend)} in the window.
          </p>
        </div>
        <AdminTable
          caption="Usage by meter"
          columns={meterColumns}
          rows={meters.map((m) => ({ ...m, id: m.meter }))}
          nextHref={null}
          firstHref={null}
        />
      </div>
    </div>
  );
}
