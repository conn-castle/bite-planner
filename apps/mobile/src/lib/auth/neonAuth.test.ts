import type { HttpOptions, HttpResponse } from '@capacitor/core'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { AuthClient, User } from './auth'

const native = vi.hoisted(() => ({
  enabled: false,
  request: vi.fn<(options: HttpOptions) => Promise<HttpResponse>>(),
}))

vi.mock('@capacitor/core', () => ({
  Capacitor: { isNativePlatform: (): boolean => native.enabled },
  CapacitorHttp: { request: native.request },
}))
vi.mock('../log', () => ({ logger: { warn: vi.fn() } }))

const origin: string = 'https://app.example.test'
const authUrl: string = 'https://auth.example.test/neondb/auth'
const email: string = 'cook@example.test'
const jwt: string = 'eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjE4OTM0NTYwMDB9.synthetic'
let user: User
let signedIn: boolean
let rejectCode: boolean
let methods: string[]
let activeClient: AuthClient | undefined

/** Provider HTTP contract; the Neon and Better Auth clients themselves are real. */
function respond(url: string, method: string, body: unknown): Response {
  const path: string = url.slice(authUrl.length).split('?')[0] ?? ''
  methods.push(path)
  switch (path) {
    case '/email-otp/send-verification-otp':
      expect(method).toBe('POST')
      expect(body).toEqual({ email, type: 'sign-in' })
      return Response.json({ success: true })
    case '/sign-in/email-otp':
      expect(method).toBe('POST')
      expect(body).toEqual({ email, otp: '123456' })
      if (rejectCode) return Response.json({ code: 'INVALID_OTP', message: 'Invalid OTP' }, { status: 400 })
      signedIn = true
      return Response.json({ token: 'synthetic-sign-in-token', user })
    case '/get-session':
      return Response.json(signedIn ? {
        user: { ...user, emailVerified: true, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z' },
        session: {
          id: 'synthetic-session', userId: user.id, token: 'opaque-session-token',
          expiresAt: '2030-01-01T00:00:00Z', createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
        },
      } : null, { headers: signedIn ? { 'set-auth-jwt': jwt } : {} })
    case '/update-user':
      expect(body).toEqual({ name: 'Updated Cook' })
      user = { ...user, name: 'Updated Cook' }
      return Response.json({ status: true })
    case '/sign-out':
      signedIn = false
      return Response.json({ success: true })
    default:
      throw new Error(`Unexpected auth request: ${method} ${url}`)
  }
}

beforeEach((): void => {
  vi.resetModules()
  vi.stubEnv('DEV', false)
  vi.stubEnv('VITE_NEON_AUTH_URL', authUrl)
  vi.stubEnv('VITE_NEON_AUTH_TRUSTED_ORIGIN', origin)
  user = { id: 'synthetic-user', email, name: 'Cook' }
  signedIn = false
  rejectCode = false
  methods = []
  activeClient = undefined
  native.enabled = false
  native.request.mockReset()
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init): Promise<Response> => {
    if (native.enabled) throw new Error('An iOS auth request bypassed native HTTP')
    const url: string = input instanceof Request ? input.url : input.toString()
    const body: unknown = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
    return respond(url, init?.method ?? 'GET', body)
  }))
})

afterEach(async (): Promise<void> => {
  try {
    // Neon caches sessions across clients; a failed test must not sign in the next.
    await activeClient?.signOut()
  } finally {
    vi.unstubAllGlobals()
    vi.unstubAllEnvs()
  }
})

it.each([false, true])('preserves email-code sign-in, JWT access, name changes and sign-out (native=%s)', async (isNative: boolean): Promise<void> => {
  native.enabled = isNative
  if (isNative) {
    // A browser's window.fetch and global fetch are the same property.
    vi.stubGlobal('window', globalThis)
    vi.stubGlobal('location', new URL(origin))
    vi.stubGlobal('localStorage', { getItem: (): null => null, setItem: (): void => {}, removeItem: (): void => {} })
    native.request.mockImplementation(async (options: HttpOptions): Promise<HttpResponse> => {
      expect(options.headers?.origin).toBe(origin)
      const response: Response = respond(options.url, options.method ?? 'GET', options.data)
      const data: unknown = await response.json()
      return { status: response.status, headers: Object.fromEntries(response.headers.entries()), url: options.url, data }
    })
  }
  const { neonAuth } = await import('./neonAuth')
  activeClient = neonAuth

  expect(await neonAuth.getUser()).toBeNull()
  expect(await neonAuth.getToken()).toBeNull()
  await neonAuth.sendCode(email)
  await neonAuth.verifyCode(email, '123456')
  expect(await neonAuth.getUser()).toEqual(user)
  expect(await neonAuth.getToken()).toBe(jwt)
  await neonAuth.updateName('Updated Cook')
  expect(await neonAuth.getUser()).toEqual({ ...user, name: 'Updated Cook' })
  await neonAuth.signOut()
  expect(await neonAuth.getUser()).toBeNull()
  expect(await neonAuth.getToken()).toBeNull()
  expect(methods).toContain('/email-otp/send-verification-otp')
  expect(native.request.mock.calls.length > 0).toBe(isNative)
})

it('rejects an incorrect sign-in code without returning a user or bearer token', async (): Promise<void> => {
  native.enabled = false
  rejectCode = true
  const { neonAuth } = await import('./neonAuth')
  activeClient = neonAuth

  await expect(neonAuth.verifyCode(email, '123456')).rejects.toThrow()
  expect(await neonAuth.getUser()).toBeNull()
  expect(await neonAuth.getToken()).toBeNull()
})
