import { auth } from '../auth/auth'
import { ApiError } from './errors'

/** HTTP client for the Castle Chef API (backend/src/castle_chef/api.py). */

function baseUrl(): string {
  const url = import.meta.env.VITE_API_URL
  if (!url) throw new Error('Missing VITE_API_URL; see apps/mobile/.env*')
  return url.replace(/\/+$/, '')
}

// The phone's IANA time zone (e.g. `America/New_York`), sent with every request:
// the API uses it wherever it needs "today" (e.g. the earliest delivery date).
function timeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone
}

/** Sends a request with a fresh token; on 401 (e.g. an expired token), retries once. */
export async function apiRequest<T>(
  method: 'GET' | 'POST' | 'PUT' | 'DELETE',
  path: string,
  body?: unknown,
): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    const token = await auth.getToken()
    if (!token) throw new ApiError(401, 'Sign in first.')
    const response = await fetch(`${baseUrl()}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        'Time-Zone': timeZone(),
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
    return (response.status === 204 ? undefined : await response.json()) as T
  }
}
