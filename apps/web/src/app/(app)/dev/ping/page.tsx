import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { PingDemo } from '@/features/dev-ping/ping-demo';

export const metadata: Metadata = { title: 'Job pipeline check' };

export default function PingPage() {
  if (process.env.NODE_ENV === 'production') notFound();
  return <PingDemo />;
}
