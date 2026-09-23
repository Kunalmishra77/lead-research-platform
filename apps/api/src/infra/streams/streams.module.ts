import { Global, Module } from '@nestjs/common';

import { ProgressHub } from '../../common/sse/progress-hub';
import { JobPublisher } from './job-publisher';
import { JobRpc } from './job-rpc';

@Global()
@Module({
  providers: [JobPublisher, JobRpc, ProgressHub],
  exports: [JobPublisher, JobRpc, ProgressHub],
})
export class StreamsModule {}
