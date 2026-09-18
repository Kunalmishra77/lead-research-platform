import { Module } from '@nestjs/common';

import { DevOnlyGuard } from './dev-only.guard';
import { PingController } from './ping.controller';
import { PingService } from './ping.service';

@Module({
  controllers: [PingController],
  providers: [PingService, DevOnlyGuard],
})
export class DevModule {}
