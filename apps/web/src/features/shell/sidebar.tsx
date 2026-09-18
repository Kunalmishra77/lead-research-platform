'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { cn } from '@/lib/utils';

import type { NavItem } from './nav';

function NavLink({ item, active }: { item: NavItem; active: boolean }) {
  const Icon = item.icon;
  const base = 'flex h-9 items-center gap-3 rounded-md px-3 text-sm transition-colors';
  if (item.soon) {
    return (
      <span
        className={cn(base, 'cursor-not-allowed text-muted-foreground/60')}
        aria-disabled="true"
      >
        <Icon aria-hidden className="size-4" />
        <span className="flex-1">{item.label}</span>
        <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide">
          Soon
        </span>
      </span>
    );
  }
  return (
    <Link
      href={item.href}
      aria-current={active ? 'page' : undefined}
      className={cn(
        base,
        active
          ? 'bg-accent font-medium text-accent-foreground'
          : 'text-sidebar-foreground hover:bg-accent/60 hover:text-foreground',
      )}
    >
      <Icon aria-hidden className="size-4" />
      {item.label}
    </Link>
  );
}

export function SidebarNav({ sections }: { sections: { label?: string; items: NavItem[] }[] }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Main" className="flex flex-col gap-6">
      {sections.map((section, i) => (
        <div key={section.label ?? i} className="flex flex-col gap-1">
          {section.label ? (
            <p className="px-3 pb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {section.label}
            </p>
          ) : null}
          {section.items.map((item) => (
            <NavLink
              key={item.href}
              item={item}
              active={pathname === item.href || pathname.startsWith(`${item.href}/`)}
            />
          ))}
        </div>
      ))}
    </nav>
  );
}
