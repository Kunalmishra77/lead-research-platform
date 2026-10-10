import { Module } from '@nestjs/common';

import { CreditsModule } from '../credits/credits.module';
import { DashboardController } from './dashboard.controller';
import { DashboardService } from './dashboard.service';

@Module({
  imports: [CreditsModule],
  controllers: [DashboardController],
  providers: [DashboardService],
})
export class DashboardModule {}
