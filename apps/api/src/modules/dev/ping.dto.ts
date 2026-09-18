import { createZodDto } from 'nestjs-zod';
import { z } from 'zod';

export const CreatePingSchema = z.object({
  message: z.string().max(200).default('ping'),
  delayMs: z.number().int().min(0).max(60_000).default(1_500),
  steps: z.number().int().min(1).max(20).default(3),
  /** Fail transiently while attempt <= failTimes (retries; 5 = dead-lettered). */
  failTimes: z.number().int().min(0).max(5).default(0),
});
export class CreatePingDto extends createZodDto(CreatePingSchema) {}

export const JobIdParamSchema = z.object({ id: z.uuid() });
export class JobIdParamDto extends createZodDto(JobIdParamSchema) {}

export interface JobRunView {
  id: string;
  type: string;
  status: string;
  attempts: number;
  result: unknown;
  errorClass: string | null;
  error: string | null;
  createdAt: string;
  finishedAt: string | null;
}
