import { Global, Module } from '@nestjs/common';

import { AuditService } from './audit.service';
import { SessionAuditService } from './session-audit.service';

@Global()
@Module({
  providers: [AuditService, SessionAuditService],
  exports: [AuditService, SessionAuditService],
})
export class AuditModule {}
