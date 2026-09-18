import { HttpException, HttpStatus } from '@nestjs/common';
import { ZodValidationException } from 'nestjs-zod';

import { AppError, type ErrorClass } from './app-error';

export const PROBLEM_TYPE_BASE = 'https://leadforge.dev/problems/';

/** RFC 7807 body. `request_id` lets support find the matching log lines. */
export interface ProblemDetails {
  type: string;
  title: string;
  status: number;
  code: string;
  detail?: string;
  request_id: string;
  errors?: { path: string; message: string }[];
}

export interface MappedProblem {
  body: ProblemDetails;
  errorClass: ErrorClass | undefined;
  /** True for 5xx: log at error level with the stack. */
  unexpected: boolean;
}

const STATUS_CODES: Record<number, string> = {
  400: 'bad_request',
  401: 'unauthorized',
  402: 'insufficient_credits',
  403: 'forbidden',
  404: 'not_found',
  405: 'method_not_allowed',
  409: 'conflict',
  413: 'payload_too_large',
  415: 'unsupported_media_type',
  422: 'unprocessable_entity',
  429: 'rate_limited',
  503: 'service_unavailable',
};

function problem(status: number, code: string, title: string, requestId: string, detail?: string) {
  const body: ProblemDetails = {
    type: PROBLEM_TYPE_BASE + code,
    title,
    status,
    code,
    request_id: requestId,
  };
  if (detail !== undefined) body.detail = detail;
  return body;
}

function zodIssues(error: unknown): { path: string; message: string }[] {
  const issues = (error as { issues?: { path?: PropertyKey[]; message?: string }[] }).issues ?? [];
  return issues.map((i) => ({
    path: (i.path ?? []).map(String).join('.') || '(root)',
    message: i.message ?? 'invalid',
  }));
}

/** Maps any thrown value to a problem+json body. Never leaks internal messages for 5xx. */
export function toProblem(exception: unknown, requestId: string): MappedProblem {
  if (exception instanceof AppError) {
    return {
      body: problem(
        exception.httpStatus,
        exception.code,
        exception.title,
        requestId,
        exception.detail,
      ),
      errorClass: exception.errorClass,
      unexpected: exception.httpStatus >= 500,
    };
  }
  if (exception instanceof ZodValidationException) {
    const body = problem(400, 'invalid_input', 'Request validation failed', requestId);
    body.errors = zodIssues(exception.getZodError());
    return { body, errorClass: 'invalid_input', unexpected: false };
  }
  if (exception instanceof HttpException) {
    const status = exception.getStatus();
    const code = STATUS_CODES[status] ?? (status >= 500 ? 'internal_error' : 'http_error');
    // Nest's default 404 message echoes the request path; use a fixed text instead.
    const detail =
      status >= 500
        ? undefined
        : status === 404 && exception.message.startsWith('Cannot ')
          ? 'No route matches this request'
          : exception.message;
    return {
      body: problem(
        status,
        code,
        HttpStatus[status] ? humanize(HttpStatus[status]) : 'Error',
        requestId,
        detail,
      ),
      errorClass: status === 429 ? 'rate_limited' : undefined,
      unexpected: status >= 500,
    };
  }
  // Only Fastify's own client errors (code FST_*, e.g. body too large) keep their message; any
  // other error shaped like { statusCode } (SDKs, http-errors) is treated as internal.
  const { statusCode, code: errorCode } = exception as { statusCode?: unknown; code?: unknown };
  if (
    typeof errorCode === 'string' &&
    errorCode.startsWith('FST_') &&
    typeof statusCode === 'number' &&
    statusCode >= 400 &&
    statusCode < 500
  ) {
    const code = STATUS_CODES[statusCode] ?? 'http_error';
    return {
      body: problem(statusCode, code, humanize(code), requestId, (exception as Error).message),
      errorClass: 'invalid_input',
      unexpected: false,
    };
  }
  return {
    body: problem(500, 'internal_error', 'Internal server error', requestId),
    errorClass: undefined,
    unexpected: true,
  };
}

function humanize(value: string): string {
  const words = value.toLowerCase().replaceAll('_', ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}
