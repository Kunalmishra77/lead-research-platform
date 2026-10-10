import { Button } from '@/components/ui/button';

import { rerunResearch } from '../actions';

/**
 * Runs an earlier search again, with the spec it was run with.
 *
 * A form rather than an onClick: this spends credits, so it is a POST, and a POST that works
 * without JavaScript is one fewer way for a button to silently do nothing. The confirmation is
 * the job page it lands on, which shows the reservation before any work starts.
 */
export function RerunButton({ jobId }: { jobId: string }) {
  return (
    <form action={rerunResearch}>
      <input name="jobId" type="hidden" value={jobId} />
      <Button size="sm" title="Start a new run with the same request" type="submit" variant="ghost">
        Run again
      </Button>
    </form>
  );
}
