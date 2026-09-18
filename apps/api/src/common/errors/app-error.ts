/** Error classes from CLAUDE.md "No silent failures", used for jobs and logged on API errors. */
export type ErrorClass =
  | 'transient'
  | 'rate_limited'
  | 'access_restricted'
  | 'parse_failed'
  | 'invalid_input'
  | 'budget_exhausted';

export interface AppErrorOptions {
  /** Stable machine code, e.g. `research.not_found`. Part of the public error contract. */
  code: string;
  httpStatus: number;
  title: string;
  detail?: string;
  errorClass?: ErrorClass;
  cause?: unknown;
}

/** Typed application error; mapped to RFC 7807 problem+json by ProblemDetailsFilter. */
export class AppError extends Error {
  readonly code: string;
  readonly httpStatus: number;
  readonly title: string;
  readonly detail: string | undefined;
  readonly errorClass: ErrorClass | undefined;

  constructor(options: AppErrorOptions) {
    super(options.detail ?? options.title, { cause: options.cause });
    this.name = 'AppError';
    this.code = options.code;
    this.httpStatus = options.httpStatus;
    this.title = options.title;
    this.detail = options.detail;
    this.errorClass = options.errorClass;
  }
}
