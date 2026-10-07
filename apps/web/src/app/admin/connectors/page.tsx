import type { Metadata } from 'next';

import { AdminTable, type Column } from '@/features/admin/admin-table';
import {
  type AdminConnector,
  type AdminMeter,
  connectorHealth,
  usageByMeter,
} from '@/features/admin/api';
import { formatDateTime, formatDuration, formatMicros } from '@/features/admin/format';

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

export default async function AdminConnectorsPage() {
  const [connectors, meters] = await Promise.all([connectorHealth(), usageByMeter()]);
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
