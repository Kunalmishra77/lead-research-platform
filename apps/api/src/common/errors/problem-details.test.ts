import { ForbiddenException, NotFoundException, ServiceUnavailableException } from '@nestjs/common';
import { describe, expect, it } from 'vitest';

import { AppError } from './app-error';
import { toProblem } from './problem-details';

describe('toProblem', () => {
  it('maps AppError with its error class', () => {
    const mapped = toProblem(
      new AppError({
        code: 'credits.insufficient',
        httpStatus: 402,
        title: 'Not enough credits',
        errorClass: 'budget_exhausted',
      }),
      'req-1',
    );
    expect(mapped.body).toEqual({
      type: 'https://leadforge.dev/problems/credits.insufficient',
      title: 'Not enough credits',
      status: 402,
      code: 'credits.insufficient',
      request_id: 'req-1',
    });
    expect(mapped.errorClass).toBe('budget_exhausted');
    expect(mapped.unexpected).toBe(false);
  });

  it('maps Nest HttpExceptions to stable codes', () => {
    expect(toProblem(new ForbiddenException(), 'r').body).toMatchObject({
      status: 403,
      code: 'forbidden',
    });
  });

  it('hides details of 5xx HttpExceptions', () => {
    const mapped = toProblem(new ServiceUnavailableException('db password wrong'), 'r');
    expect(mapped.body.detail).toBeUndefined();
    expect(mapped.unexpected).toBe(true);
  });

  it('maps Fastify client errors (FST_*) by statusCode', () => {
    const err = Object.assign(new Error('Request body is too large'), {
      statusCode: 413,
      code: 'FST_ERR_CTP_BODY_TOO_LARGE',
    });
    expect(toProblem(err, 'r').body).toMatchObject({ status: 413, code: 'payload_too_large' });
  });

  it('does not trust statusCode on non-Fastify errors', () => {
    const err = Object.assign(new Error('S3 AccessDenied for key secret/path'), {
      statusCode: 403,
    });
    const mapped = toProblem(err, 'r');
    expect(mapped.body).toMatchObject({ status: 500, code: 'internal_error' });
    expect(JSON.stringify(mapped.body)).not.toContain('secret/path');
  });

  it('does not echo the request path in 404 details', () => {
    const mapped = toProblem(new NotFoundException('Cannot GET /admin/secret-route'), 'r');
    expect(mapped.body.detail).toBe('No route matches this request');
  });

  it('treats unknown values as internal errors', () => {
    const mapped = toProblem('boom', 'r');
    expect(mapped.body).toMatchObject({ status: 500, code: 'internal_error' });
    expect(mapped.unexpected).toBe(true);
  });
});
