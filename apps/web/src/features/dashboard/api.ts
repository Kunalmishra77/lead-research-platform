import 'server-only';

import { apiFetch } from '@/lib/api/server';

import type { DashboardView } from './types';

export function getDashboard(): Promise<DashboardView> {
  return apiFetch<DashboardView>('/app/dashboard');
}
