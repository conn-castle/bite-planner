import { isRecord } from './isRecord'

/** What went wrong, in words: an error's message (neon-js also gives plain `{ message }` objects), or the value itself. */
export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  if (isRecord(error) && 'message' in error) return String(error.message)
  return String(error)
}
