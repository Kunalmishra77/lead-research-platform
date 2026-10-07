import type { AdminTask } from './api';

export interface TaskNode {
  task: AdminTask;
  children: TaskNode[];
}

/**
 * The fan-out of a job, as a tree. A research job plans once and then fans out a task per search,
 * so the shape is shallow — but it is the shape that answers "did the planner produce anything at
 * all?", which a flat list cannot.
 *
 * Two cases are handled deliberately rather than by accident:
 *
 * - **Orphans become roots.** `admin_job_tasks` caps at 2000 rows, so a very large job can lose a
 *   parent off the end. Dropping its children would make the job look emptier than it was, which
 *   is the opposite of what this page is for.
 * - **A task that is its own parent is a root.** It should not happen, but a tree builder that
 *   trusts the data recurses for ever and takes the page down with it.
 */
export function toTaskTree(tasks: readonly AdminTask[]): TaskNode[] {
  const nodes = new Map<string, TaskNode>(tasks.map((task) => [task.id, { task, children: [] }]));
  const roots: TaskNode[] = [];
  for (const node of nodes.values()) {
    const parent = node.task.parentTaskId ? nodes.get(node.task.parentTaskId) : undefined;
    if (parent && parent !== node) parent.children.push(node);
    else roots.push(node);
  }
  return roots;
}

/**
 * What a task was asked to do. The input holds a query and an area, never a credential, and it is
 * the first thing to read when a task returned nothing. Only scalars are shown: a nested object
 * printed raw is noise, and this is a one-line summary.
 */
export function inputSummary(input: Record<string, unknown>): string | null {
  const parts = Object.entries(input)
    .filter(([, v]) => typeof v === 'string' || typeof v === 'number')
    .map(([k, v]) => `${k}=${String(v)}`);
  return parts.length > 0 ? parts.join(' · ') : null;
}
