import type { ComponentProps } from 'react';

import { cn } from '@/lib/utils';

export function Textarea({ className, ...props }: ComponentProps<'textarea'>) {
  return (
    <textarea
      className={cn(
        'border-input placeholder:text-muted-foreground focus-visible:ring-ring/50 aria-invalid:border-danger',
        'flex min-h-24 w-full resize-y rounded-lg border bg-transparent px-3 py-2 text-sm shadow-xs',
        'transition-[color,box-shadow] outline-none focus-visible:ring-[3px]',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    />
  );
}
