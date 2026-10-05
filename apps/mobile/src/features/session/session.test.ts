import { QueryClient } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiRequest } from '../../lib/api/client'
import type { Me } from '../../lib/api/types'
import { sessionQuery } from './session'
import { signOut } from './signOut'

vi.mock('../../lib/api/client', () => ({ apiRequest: vi.fn(), phoneTimeZone: () => 'Europe/Lisbon' }))
vi.mock('../../lib/log', () => ({ logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() }, errorFields: () => ({}) }))
vi.mock('../../lib/auth/auth', () => ({
  auth: { getUser: async () => ({ id: 'sam', email: 'sam@example.com', name: 'Sam' }), signOut: async () => {} },
}))
vi.mock('../../lib/logUpload', () => ({ uploadLogs: vi.fn() }))
vi.mock('../../lib/query/queryClient', () => ({ persister: { removeClient: async () => {} } }))
const request = vi.mocked(apiRequest)

const me = (timeZone: string | null): Me => ({
  user_id: 'sam',
  email: 'sam@example.com',
  name: 'Sam',
  member: true,
  household: { id: 'castle', name: 'Conn Castle' },
  settings: { notification_time: '08:00:00', time_zone: timeZone, reminders_on: true },
})

let queryClient: QueryClient

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})

afterEach(() => request.mockReset())

describe("the phone's time zone", () => {
  it('is recorded when the member has none yet, or the phone has moved', async () => {
    for (const stored of [null, 'America/New_York']) {
      request.mockReset()
      request.mockImplementation(async (method) =>
        method === 'GET' ? me(stored) : { notification_time: '08:00:00', time_zone: 'Europe/Lisbon', reminders_on: true },
      )

      const session = await queryClient.fetchQuery({ ...sessionQuery, staleTime: 0 })

      expect(request.mock.calls).toEqual([
        ['GET', '/v1/me'],
        ['PATCH', '/v1/me/settings', { time_zone: 'Europe/Lisbon' }],
      ])
      expect(session?.settings?.time_zone).toBe('Europe/Lisbon')
    }
  })

  it("isn't sent again when it hasn't changed", async () => {
    request.mockResolvedValue(me('Europe/Lisbon'))

    await queryClient.fetchQuery(sessionQuery)

    expect(request.mock.calls).toEqual([['GET', '/v1/me']])
  })

  it("doesn't keep the session from loading when it can't be recorded", async () => {
    request.mockImplementation(async (method) => {
      if (method === 'PATCH') throw new Error('offline')
      return me('America/New_York')
    })

    const session = await queryClient.fetchQuery(sessionQuery)

    expect(session?.settings?.time_zone).toBe('America/New_York')
  })

  it("isn't asked for from someone who isn't in a household", async () => {
    request.mockResolvedValue({ ...me(null), member: false, household: null, settings: null })

    const session = await queryClient.fetchQuery(sessionQuery)

    expect(session?.settings).toBeNull()
    expect(request.mock.calls).toEqual([['GET', '/v1/me']])
  })
})

describe('signing out', () => {
  it("isn't undone by a session fetch still on its way", async () => {
    request.mockImplementation(async (method) => {
      await new Promise((resolve) => setTimeout(resolve, 20))
      return method === 'GET' ? me('America/New_York') : { notification_time: '08:00:00', time_zone: 'Europe/Lisbon', reminders_on: true }
    })
    const fetching = queryClient.fetchQuery(sessionQuery).catch(() => undefined)

    await signOut(queryClient)
    await fetching

    expect(queryClient.getQueryData(sessionQuery.queryKey)).toBeNull()
  })
})
