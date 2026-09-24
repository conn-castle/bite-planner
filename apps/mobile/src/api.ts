import { ApiError } from './apiError'
import { auth } from './auth'
import type { LogEntry } from './log'
import type { Job, Meal } from './types'

/** The Castle Chef API (backend/src/castle_chef/api.py). */

function apiUrl(): string {
  const url = import.meta.env.VITE_API_URL
  if (!url) throw new Error('Missing VITE_API_URL; see apps/mobile/.env*')
  return url.replace(/\/+$/, '')
}




/** Sends a request with a fresh token; on 401 (e.g. an expired token), retries once. */
async function request<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    const token = await auth.getToken()
    if (!token) throw new ApiError(401, 'Sign in first.')
    const response = await fetch(`${apiUrl()}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
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

export interface Me {
  user_id: string
  email: string | null
  name: string | null
  member: boolean
}

export const getMe = () => request<Me>('GET', '/v1/me')

export const listJobs = () => request<Job[]>('GET', '/v1/jobs')

export interface MealRequest {
  /** Chosen here, so a retried request (e.g. resumed after going offline) isn't duplicated. */
  jobId: string
  count: number
}

export async function requestMeals({ jobId, count }: MealRequest): Promise<void> {
  await request<Job>('POST', '/v1/jobs', { job_id: jobId, count })
}

export const listJobMeals = (jobId: string) => request<Meal[]>('GET', `/v1/jobs/${jobId}/meals`)

/** Sends the app's logs; they land in CloudWatch next to the backend's. */
export async function sendLogs(entries: LogEntry[], device: Record<string, unknown>): Promise<void> {
  // The API accepts at most 2000 entries per batch.
  await request<void>('POST', '/v1/logs', { device, entries: entries.slice(-2000) })
}
