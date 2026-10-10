import Link from 'next/link';

import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

import type { DashboardView } from '../types';

/**
 * The overview, built only from things the system recorded.
 *
 * There is no lead score, no verification rate and no hot/warm/cold split here, because scoring
 * (Phase 6) and verification (Phase 4) do not exist yet. A dashboard that shows those anyway is
 * not a nicer dashboard, it is a lying one — and a person who acts on "87% verified" when nothing
 * has been verified is worse off than a person who sees nothing at all.
 *
 * Server-rendered throughout, with no hooks: it reads once and the numbers do not animate.
 */
export function Overview({ data }: { data: DashboardView }) {
  const { leads, reachable, jobs, credits, sources, recentRuns } = data;
  return (
    <div className="space-y-6">
      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          hint={leads.today > 0 ? `${fmt(leads.today)} today` : 'none today'}
          label="Leads"
          value={leads.total}
        />
        <Stat
          hint={`${fmt(leads.last7Days)} in the last 7 days`}
          label="Last 30 days"
          value={leads.last30Days}
        />
        <Stat
          hint={jobs.running + jobs.queued > 0 ? 'a run is working now' : lastRun(jobs)}
          label="Runs working"
          value={jobs.running + jobs.queued}
        />
        <Stat
          hint={`${fmt(credits.spentLast30Days)} spent in 30 days`}
          label="Credits"
          value={credits.balance}
        />
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>How many you can reach</CardTitle>
            <CardDescription>
              Of {fmt(leads.total)} lead{leads.total === 1 ? '' : 's'} in this workspace. A name and
              a map pin is something anyone can get; an address someone answers is not.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Share label="Email" total={leads.total} value={reachable.withEmail} />
            <Share label="Phone" total={leads.total} value={reachable.withPhone} />
            <Share label="Website" total={leads.total} value={reachable.withWebsite} />
            <Share label="Social profile" total={leads.total} value={reachable.withSocial} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Where the facts came from</CardTitle>
            <CardDescription>
              Values in force for this workspace&rsquo;s companies, by source.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {sources.length === 0 ? (
              <p className="text-muted-foreground text-sm">Nothing stored yet.</p>
            ) : (
              <div className="space-y-3">
                {sources.map((s) => (
                  <Share
                    key={s.source}
                    label={sourceName(s.source)}
                    total={sources.reduce((sum, x) => sum + x.values, 0)}
                    value={s.values}
                  />
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-baseline justify-between gap-3 space-y-0">
          <div className="space-y-1">
            <CardTitle>Recent runs</CardTitle>
            <CardDescription>
              What each one found, and how much of it the workspace did not already have.
            </CardDescription>
          </div>
          <Link
            className="text-muted-foreground hover:text-foreground text-xs underline underline-offset-2"
            href="/research/history"
          >
            All research
          </Link>
        </CardHeader>
        <CardContent>
          {recentRuns.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No runs yet.{' '}
              <Link className="underline underline-offset-2" href="/research/new">
                Describe the leads you need
              </Link>
              .
            </p>
          ) : (
            <ul className="divide-border divide-y">
              {recentRuns.map((run) => (
                <li key={run.id} className="flex flex-wrap items-center gap-3 py-2 first:pt-0">
                  <Link
                    className="hover:text-foreground min-w-0 flex-1 truncate text-sm underline-offset-2 hover:underline"
                    href={`/research/${run.id}`}
                  >
                    {run.rawQuery}
                  </Link>
                  <Badge variant={badgeFor(run.status)}>{run.status}</Badge>
                  <span className="text-muted-foreground w-36 text-right text-xs tabular-nums">
                    {run.leads > 0
                      ? `${fmt(run.leads)} found · ${fmt(run.newLeads)} new`
                      : 'nothing delivered'}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: number; hint: string }) {
  return (
    <Card>
      <CardContent className="space-y-1 p-4">
        <p className="text-muted-foreground text-xs">{label}</p>
        <p className="text-2xl font-semibold tabular-nums">{fmt(value)}</p>
        <p className="text-muted-foreground text-xs">{hint}</p>
      </CardContent>
    </Card>
  );
}

/**
 * One row of a bar chart, which is all a four-way split needs.
 *
 * A bar rather than a doughnut: the question is "how much of the whole", and a length answers it
 * at a glance where an angle does not. The number is written out beside it either way, because a
 * bar nobody can read a value off is decoration.
 */
export function Share({ label, value, total }: { label: string; value: number; total: number }) {
  const pct = total > 0 ? Math.round((value / total) * 100) : 0;
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span>{label}</span>
        <span className="text-muted-foreground tabular-nums">
          {fmt(value)} <span className="text-xs">({pct}%)</span>
        </span>
      </div>
      <div
        aria-hidden
        className="bg-muted h-1.5 w-full overflow-hidden rounded-full"
        // Inline width because the value is data, not a design token: Tailwind cannot generate a
        // class per percentage and rounding to the nearest step would misreport the number.
      >
        <div className="bg-primary h-full rounded-full" style={{ width: `${String(pct)}%` }} />
      </div>
    </div>
  );
}

export function fmt(n: number): string {
  return n.toLocaleString('en-IN');
}

/** Source keys are machine names; this is the one place they are read by a person. */
export function sourceName(key: string): string {
  if (key === 'google_places') return 'Google Maps';
  if (key === 'website') return "The company's own site";
  return key.replace(/_/g, ' ');
}

function badgeFor(status: string): 'success' | 'danger' | 'warning' | 'info' {
  if (status === 'completed') return 'success';
  if (status === 'failed') return 'danger';
  if (status === 'cancelled' || status === 'paused') return 'warning';
  return 'info';
}

function lastRun(jobs: DashboardView['jobs']): string {
  if (!jobs.lastFinishedAt) return 'nothing has run yet';
  return `last finished ${new Date(jobs.lastFinishedAt).toLocaleDateString('en-IN', {
    day: 'numeric',
    month: 'short',
  })}`;
}
