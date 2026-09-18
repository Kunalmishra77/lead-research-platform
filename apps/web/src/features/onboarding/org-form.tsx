'use client';

import { useActionState } from 'react';

import { Button } from '@/components/ui/button';
import { Input, Label } from '@/components/ui/input';

import { createOrganization } from './actions';

export function OrgForm() {
  const [state, action, pending] = useActionState(createOrganization, {});
  return (
    <form action={action} className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <Label htmlFor="name">Company or team name</Label>
        <Input id="name" name="name" required maxLength={120} placeholder="Acme Digital" />
      </div>
      {state.error ? (
        <p role="alert" className="text-sm text-danger">
          {state.error}
        </p>
      ) : null}
      <Button type="submit" disabled={pending}>
        {pending ? 'Creating…' : 'Create workspace'}
      </Button>
    </form>
  );
}
