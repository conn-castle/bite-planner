import { QueryClient } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiRequest } from '../../lib/api/client'
import { ApiError } from '../../lib/api/errors'
import { registerOrderMutations } from './orders'
import { lockRequests, type LockView } from './useOrderLock'

vi.mock('../../lib/api/client', () => ({ apiRequest: vi.fn() }))
// The app's logger stores its entries on the device, which a test doesn't have.
vi.mock('../../lib/log', () => ({ logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() }, errorFields: vi.fn() }))
const request = vi.mocked(apiRequest)

const session = 'phone'
const later = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

let queryClient: QueryClient

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })
  registerOrderMutations(queryClient)
})

afterEach(() => request.mockReset())

const view = (shown: Pick<LockView, 'holder' | 'step'>): LockView => ({
  orderId: 'draft',
  session,
  hadLock: true,
  ...shown,
})

describe("the flow's lock requests", () => {
  it('take the lock again when an order placed while it renewed is changed back into the draft', async () => {
    const requests = lockRequests(queryClient)
    request.mockRejectedValueOnce(new ApiError(409, 'This order has been placed.'))
    requests.tick(view({ holder: 'this', step: 'review' }))
    await later(0)

    // "Change order" on the placed screen: the draft again, with no lock.
    request.mockResolvedValueOnce({})
    requests.tick(view({ holder: 'none', step: 'review' }))
    await later(0)

    expect(request.mock.calls).toEqual([
      ['PUT', '/v1/grocery-orders/draft/lock', { step: 'review' }, session],
      ['POST', '/v1/grocery-orders/draft/lock', { step: 'review' }, session],
    ])
  })

  it('send nothing more once the order is gone', async () => {
    const requests = lockRequests(queryClient)
    request.mockRejectedValueOnce(new ApiError(404, 'Not found.'))
    requests.tick(view({ holder: 'this', step: 'pick' }))
    await later(0)

    // The discarded draft is still the copy on screen.
    requests.tick(view({ holder: 'this', step: 'pick' }))
    await later(0)

    expect(request.mock.calls).toEqual([['PUT', '/v1/grocery-orders/draft/lock', { step: 'pick' }, session]])
  })
})
