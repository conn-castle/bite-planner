/** An answer from the API other than success (its status and `detail`). */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/**
 * Whether a failed request is worth sending again unchanged: only when it may
 * never have reached the API (a network error). The API answered everything
 * else, and repeating it would get the same answer.
 */
export function isRetryable(error: unknown, failureCount: number): boolean {
  return !(error instanceof ApiError) && failureCount < 3
}
