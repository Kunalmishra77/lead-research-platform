import { type CanActivate, Inject, Injectable, NotFoundException } from '@nestjs/common';

import { APP_CONFIG } from '../../config/config.module';
import type { AppConfig } from '../../config/env.schema';

/** Hides development routes in production (they answer 404 as if they did not exist). */
@Injectable()
export class DevOnlyGuard implements CanActivate {
  constructor(@Inject(APP_CONFIG) private readonly config: AppConfig) {}

  canActivate(): boolean {
    if (this.config.NODE_ENV === 'production') throw new NotFoundException();
    return true;
  }
}
