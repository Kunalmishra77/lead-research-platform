import type { Metadata } from 'next';
import Link from 'next/link';

import { jobTasks } from '@/features/admin/api';
import { formatDateTime, formatDuration } from '@/features/admin/format';
import { inputSummary, type TaskNode, toTaskTree } from '@/features/admin/task-tree';

export const metadata: Metadata = { title: 'Job tasks' };

const STATUS_TONE: Record<string, string> = {
  completed: 'text-success',
  failed: 'text-danger',
  cancelled: 'text-muted-foreground',
  skipped: 'text-muted-foreground',
};

function TaskRow({ node, depth }: { node: TaskNode; depth: number }) {
  const { task } = node;
  const summary = inputSummary(task.input);
  return (
    <>
      <li style={{ paddingLeft: `${depth * 1.25}rem` }} className="border-t py-2">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <code className="text-xs text-muted-foreground">{task.id.slice(0, 8)}</code>
          <span className="font-medium">{task.type}</span>
          <span className={STATUS_TONE[task.status] ?? ''}>{task.status}</span>
          {task.errorClass ? <span className="text-danger">{task.errorClass}</span> : null}
          <span className="text-muted-foreground">{formatDuration(task.durationMs)}</span>
          {task.attempts > 1 ? (
            <span className="text-muted-foreground">attempt {task.attempts}</span>
          ) : null}
          <span className="text-muted-foreground">budget {task.creditBudget}</span>
        </div>
        {summary ? (
          <p className="mt-0.5 truncate text-xs text-muted-foreground" title={summary}>
            {summary}
          </p>
        ) : null}
      </li>
      {node.children.map((child) => (
        <TaskRow key={child.task.id} node={child} depth={depth + 1} />
      ))}
    </>
  );
}

export default async function AdminJobTasksPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const tasks = await jobTasks(id);
  const roots = toTaskTree(tasks);
  const failed = tasks.filter((t) => t.status === 'failed').length;
  const last = tasks.reduce<string | null>(
    (latest, t) => (t.finishedAt && (!latest || t.finishedAt > latest) ? t.finishedAt : latest),
    null,
  );

  return (
    <div className="flex flex-col gap-4">
      <div>
        <Link href="/admin/jobs" className="text-sm text-muted-foreground hover:underline">
          ← All jobs
        </Link>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">
          Job <code className="text-xl">{id.slice(0, 8)}</code>
        </h1>
        <p className="text-sm text-muted-foreground">
          {tasks.length} task{tasks.length === 1 ? '' : 's'}
          {failed > 0 ? ` · ${failed} failed` : ''}
          {last ? ` · last finished ${formatDateTime(last)}` : ''}
        </p>
      </div>
      {tasks.length === 0 ? (
        <p className="rounded-lg border px-4 py-8 text-center text-sm text-muted-foreground">
          No tasks. A job with no tasks was never planned — the planner either did not run or
          produced an empty plan.
        </p>
      ) : (
        <ul className="rounded-lg border px-3 text-sm">
          {roots.map((root) => (
            <TaskRow key={root.task.id} node={root} depth={0} />
          ))}
        </ul>
      )}
      <p className="text-xs text-muted-foreground">
        Costs are not shown per task: the discovery handler writes a task&apos;s{' '}
        <code>cost_micros</code> as 0, so the column would always read zero. Real spend is on the{' '}
        <Link href="/admin/connectors" className="underline">
          connectors
        </Link>{' '}
        page, from <code>usage_events</code>.
      </p>
    </div>
  );
}
