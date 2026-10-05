import { Capacitor, CapacitorHttp } from '@capacitor/core'
import { createInternalNeonAuth } from '@neondatabase/neon-js/auth'
import type { AuthClient, User } from './auth'
import { isRecord } from '../isRecord'
import { logger } from '../log'

/** Neon Auth (Better Auth, emailed one-time codes) implementation of `AuthClient`. */

function requireEnv(name: keyof ImportMetaEnv): string {
  const value = import.meta.env[name]
  if (!value) throw new Error(`Missing ${name}; see apps/mobile/.env*`)
  return value
}

// The dev server proxies Neon Auth at /neon-auth (see vite.config.ts), so in a
// browser during development the session cookie is first-party.
const AUTH_URL =
  import.meta.env.DEV && !Capacitor.isNativePlatform()
    ? `${window.location.origin}/neon-auth`
    : requireEnv('VITE_NEON_AUTH_URL')

if (Capacitor.isNativePlatform()) {
  routeAuthRequestsThroughNativeHttp(requireEnv('VITE_NEON_AUTH_TRUSTED_ORIGIN'))
}

// The same client neon-js builds for the Data API; its `getJWTToken` returns the
// session's short-lived JWT, refreshing it as needed.
const neon = createInternalNeonAuth(AUTH_URL)
const client = neon.adapter

/** neon-js throws on HTTP errors and returns `error` for others; handle both. */
async function call<T extends { error?: unknown }>(action: () => Promise<T>, failure = 'Sign-in failed.'): Promise<T> {
  const result = await action()
  if (result.error) {
    const message = isRecord(result.error) && typeof result.error.message === 'string' ? result.error.message : undefined
    if (message === 'Invalid OTP') {
      throw new Error('That code is wrong or has expired. Send a new one.')
    }
    throw result.error instanceof Error ? result.error : new Error(message ?? failure)
  }
  return result
}

export const neonAuth: AuthClient = {
  async sendCode(email) {
    await call(() => client.emailOtp.sendVerificationOtp({ email, type: 'sign-in' }))
  },
  async verifyCode(email, code) {
    await call(() => client.signIn.emailOtp({ email, otp: code }))
  },
  async getUser(): Promise<User | null> {
    const { data } = await call(() => client.getSession())
    if (!data?.user) return null
    const { id, email, name } = data.user
    return { id, email, name }
  },
  getToken: () => neon.getJWTToken(),
  async updateName(name) {
    await call(() => client.updateUser({ name }), "Couldn't change your name.")
  },
  async signOut() {
    await client.signOut()
  },
}

/**
 * Neon Auth keeps the session in an HTTP-only cookie on its own host and checks
 * the Origin header. In the iOS WebView that cookie is third-party (blocked) and
 * the `capacitor://localhost` origin cannot be trusted. Sending auth requests
 * through native HTTP keeps the cookie in the native cookie store and lets the
 * app present a trusted https origin. API requests use a bearer JWT and stay on
 * the WebView's fetch.
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
