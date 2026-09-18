import { Global, Module } from '@nestjs/common';

import { ProgressHub } from '../../common/sse/progress-hub';
import { JobPublisher } from './job-publisher';

@Global()
@Module({ providers: [JobPublisher, ProgressHub], exports: [JobPublisher, ProgressHub] })
export class StreamsModule {}
