import { Capacitor, CapacitorHttp } from '@capacitor/core'
import { createClient } from '@neondatabase/neon-js'
import { type LogEntry, logger } from './log'
import type { Job, Meal } from './types'

function requireEnv(name: keyof ImportMetaEnv): string {
  const value = import.meta.env[name]
  if (!value) throw new Error(`Missing ${name}; see apps/mobile/.env`)
  return value
}

const DATA_API_URL = requireEnv('VITE_NEON_DATA_API_URL')
// The dev server proxies Neon Auth at /neon-auth (see vite.config.ts), so in a
// browser during development the session cookie is first-party.
const AUTH_URL =
  import.meta.env.DEV && !Capacitor.isNativePlatform()
    ? `${window.location.origin}/neon-auth`
    : requireEnv('VITE_NEON_AUTH_URL')

if (Capacitor.isNativePlatform()) {
  routeAuthRequestsThroughNativeHttp(requireEnv('VITE_NEON_AUTH_TRUSTED_ORIGIN'))
}

export const neon = createClient({ auth: { url: AUTH_URL }, dataApi: { url: DATA_API_URL } })

/**
 * Neon Auth keeps the session in an HTTP-only cookie on its own host and checks
 * the Origin header. In the iOS WebView that cookie is third-party (blocked) and
 * the `capacitor://localhost` origin cannot be trusted. Sending auth requests
 * through native HTTP keeps the cookie in the native cookie store and lets the
 * app present a trusted https origin. Data API requests use a bearer JWT and
 * stay on the WebView's fetch.
 */
function routeAuthRequestsThroughNativeHttp(origin: string) {
  const webFetch = window.fetch.bind(window)
  window.fetch = async (input, init) => {
    const url = input instanceof Request ? input.url : input.toString()
    if (!url.startsWith(AUTH_URL)) return webFetch(input, init)
    if (input instanceof Request) {
      // neon-js passes URL strings; fail loudly if that changes rather than
      // silently sending auth through the WebView, where it cannot work.
      throw new Error(`Unsupported Neon Auth request object for ${url}`)
    }

    const headers = Object.fromEntries(new Headers(init?.headers).entries())
    headers['origin'] = origin
    const body = init?.body
    if (body != null && typeof body !== 'string') {
      throw new Error(`Unsupported Neon Auth request body type for ${url}`)
    }
    const method = init?.method ?? 'GET'
    const response = await CapacitorHttp.request({
      url,
      method,
      headers,
      data: body ? JSON.parse(body) : undefined,
    })
    if (response.status >= 400) {
      logger.warn('auth.native_request_failed', {
        path: url.slice(AUTH_URL.length),
        method,
        status: response.status,
      })
    }
    const data = typeof response.data === 'string' ? response.data : JSON.stringify(response.data)
    return new Response(response.status === 204 ? null : data, {
      status: response.status,
      headers: response.headers,
    })
  }
}

/** Whether the signed-in account was invited to the household (RLS shows only its own row). */
export async function isHouseholdMember(): Promise<boolean> {
  const { data, error } = await neon.from('household_member').select('user_id').limit(1)
  if (error) throw new Error(error.message)
  return data.length > 0
}

export async function listJobs(): Promise<Job[]> {
  const { data, error } = await neon
    .from('job')
    .select('id, kind, params, status, error, created_at, finished_at')
    .order('created_at', { ascending: false })
    .limit(20)
  if (error) throw new Error(error.message)
  return data as Job[]
}

// Postgres insufficient_privilege: the database allows at most 3 active requests per person.
const INSUFFICIENT_PRIVILEGE = '42501'

export async function requestMeals(count: number): Promise<void> {
  const { error } = await neon.from('job').insert({ kind: 'generate_meals', params: { count } })
  if (error?.code === INSUFFICIENT_PRIVILEGE) {
    throw new Error('You already have 3 requests waiting. Try again when one finishes.')
  }
  if (error) throw new Error(`Could not request meals: ${error.message}`)
}

export async function listJobMeals(jobId: string): Promise<Meal[]> {
  const { data, error } = await neon
    .from('job_meal')
    .select('meal(*, meal_ingredient(*))')
    .eq('job_id', jobId)
  if (error) throw new Error(error.message)
  // Meals are created in the order the agent added them.
  return (data as unknown as { meal: Meal }[])
    .map((row) => row.meal)
    .sort((a, b) => a.created_at.localeCompare(b.created_at))
}

/** Sends the app's logs to the household's machine, which saves them to disk. */
export async function sendLogs(entries: LogEntry[], device: Record<string, unknown>): Promise<void> {
  // The database accepts at most 2000 entries per batch.
  const { error } = await neon.from('client_log_batch').insert({ device, entries: entries.slice(-2000) })
  if (error) throw new Error(`Could not send logs: ${error.message}`)
}
