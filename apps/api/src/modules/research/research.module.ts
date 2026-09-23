import { Module } from '@nestjs/common';

import { CreditsModule } from '../credits/credits.module';
import { ResearchController } from './research.controller';
import { ResearchService } from './research.service';

@Module({
  imports: [CreditsModule],
  controllers: [ResearchController],
  providers: [ResearchService],
})
export class ResearchModule {}
