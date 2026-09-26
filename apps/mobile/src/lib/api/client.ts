import { auth } from '../auth/auth'
import { errorFields, logger } from '../log'
import { ApiError } from './errors'

/** HTTP client for the Castle Chef API (backend/src/castle_chef/api.py). */

function baseUrl(): string {
  const url = import.meta.env.VITE_API_URL
  if (!url) throw new Error('Missing VITE_API_URL; see apps/mobile/.env*')
  return url.replace(/\/+$/, '')
}

/**
 * The phone's IANA time zone (e.g. `America/New_York`), sent with every request:
 * the API uses it wherever it needs "today" (e.g. the earliest delivery date).
 */
export function phoneTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone
}

// A request taking longer than this is logged, as a failed one is.
const SLOW_REQUEST_MS = 3000
// The log upload's own requests aren't logged: its failures would queue more entries to upload.
const UNLOGGED_PATH = '/v1/logs'

/** A request's path as logged: without ids or tokens ("/v1/grocery-orders/{id}"), or its query. */
export function requestRoute(path: string): string {
  return path
    .split('?')[0]
    .replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi, '{id}')
    .replace(/[0-9a-f]{64,}/gi, '{token}')
}

/** Sends a request with a fresh token; on 401 (e.g. an expired token), retries once. */
export async function apiRequest<T>(
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  path: string,
  body?: unknown,
): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    const token = await auth.getToken()
    if (!token) throw new ApiError(401, 'Sign in first.')
    // Timed from the send to the whole answer, so a connection that hangs (e.g.
    // just after the phone wakes) shows in the log.
    const started = performance.now()
    const logged = path !== UNLOGGED_PATH
    const fields = () => ({ method, route: requestRoute(path), ms: Math.round(performance.now() - started) })
    try {
      const response = await fetch(`${baseUrl()}${path}`, {
        method,
        headers: {
          Authorization: `Bearer ${token}`,
          'Time-Zone': phoneTimeZone(),
          ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      if (response.status === 401 && attempt === 0) continue
      if (!response.ok) {
        const detail = await response
          .json()
          .then((json: { detail?: unknown }) => json.detail)
          .catch(() => undefined)
        const message = typeof detail === 'string' ? detail : `Request failed (${response.status}).`
        throw new ApiError(response.status, message)
      }
      const answer = (response.status === 204 ? undefined : await response.json()) as T
      if (logged && performance.now() - started > SLOW_REQUEST_MS) logger.info('api.request_slow', fields())
      return answer
    } catch (error) {
      // Once per failure: refused (with its status), or never answered in full.
      if (logged) {
        const why = error instanceof ApiError ? { status: error.status } : errorFields(error)
        logger.warn('api.request_failed', { ...fields(), ...why })
      }
      throw error
    }
  }
}
