import type { ComponentProps } from 'react';

import { cn } from '@/lib/utils';

/** A shape standing in for content that is on its way (docs/09: every data view has a loading state). */
export function Skeleton({ className, ...props }: ComponentProps<'div'>) {
  return (
    <div aria-hidden className={cn('bg-muted animate-pulse rounded-md', className)} {...props} />
  );
}
