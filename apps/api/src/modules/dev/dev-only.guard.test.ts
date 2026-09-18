import { NotFoundException } from '@nestjs/common';
import { describe, expect, it } from 'vitest';

import { TEST_ENV } from '../../../test/helpers/test-app';
import { parseEnv } from '../../config/env.schema';
import { DevOnlyGuard } from './dev-only.guard';

describe('DevOnlyGuard', () => {
  it('hides dev routes in production', () => {
    const guard = new DevOnlyGuard(parseEnv({ ...TEST_ENV, NODE_ENV: 'production' }));
    expect(() => guard.canActivate()).toThrow(NotFoundException);
  });

  it('allows them in development and test', () => {
    expect(new DevOnlyGuard(parseEnv({ ...TEST_ENV, NODE_ENV: 'development' })).canActivate()).toBe(
      true,
    );
    expect(new DevOnlyGuard(parseEnv({ ...TEST_ENV })).canActivate()).toBe(true);
  });
});
