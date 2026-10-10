/** Mirrors `DashboardView` in the API (apps/api/src/modules/dashboard/dashboard.dto.ts). */
export interface DashboardView {
  leads: { total: number; today: number; last7Days: number; last30Days: number };
  reachable: {
    withEmail: number;
    withPhone: number;
    withWebsite: number;
    withSocial: number;
  };
  jobs: {
    running: number;
    queued: number;
    completed: number;
    failed: number;
    lastFinishedAt: string | null;
  };
  credits: { balance: number; spentLast30Days: number };
  sources: { source: string; values: number }[];
  recentRuns: {
    id: string;
    rawQuery: string;
    status: string;
    createdAt: string;
    leads: number;
    newLeads: number;
  }[];
}
