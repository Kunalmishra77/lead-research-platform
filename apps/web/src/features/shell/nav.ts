import {
  CreditCard,
  FlaskConical,
  Gauge,
  List,
  type LucideIcon,
  Plug,
  Repeat,
  Search,
  Settings,
  Sparkles,
  Upload,
} from 'lucide-react';

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Built in a later phase: shown but not navigable yet. */
  soon?: boolean;
}

/** Sidebar order from docs/09 "App shell". */
export const PRIMARY_NAV: NavItem[] = [
  { href: '/dashboard', label: 'Dashboard', icon: Gauge },
  { href: '/research/new', label: 'New Research', icon: Sparkles, soon: true },
  { href: '/search', label: 'Search', icon: Search, soon: true },
  { href: '/lists', label: 'Lists', icon: List, soon: true },
  { href: '/saved-searches', label: 'Saved Searches', icon: Repeat, soon: true },
  { href: '/exports', label: 'Exports', icon: Upload, soon: true },
  { href: '/integrations', label: 'Integrations', icon: Plug, soon: true },
];

export const SECONDARY_NAV: NavItem[] = [
  { href: '/billing', label: 'Billing', icon: CreditCard, soon: true },
  { href: '/settings', label: 'Settings', icon: Settings, soon: true },
];

export const DEV_NAV: NavItem[] = [
  { href: '/dev/ping', label: 'Job pipeline check', icon: FlaskConical },
];
