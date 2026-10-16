import { MutationObserver, QueryClient } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiRequest } from '../../lib/api/client'
import { ApiError } from '../../lib/api/errors'
import type { GroceryOrder, Job, Meal, NextOrder } from '../../lib/api/types'
import { queryKeys } from '../../lib/query/keys'
import type { SetMeal, SetNotForUs, SetServings } from './edits'
import {
  closeFlowSession,
  FlowClosed,
  nextQuery,
  type OpenOrder,
  type PlacedDates,
  type PlaceOrder,
  registerOrderMutations,
  type StartJob,
  takeLock,
} from './orders'

vi.mock('../../lib/api/client', () => ({ apiRequest: vi.fn() }))
// The app's logger stores its entries on the device, which a test doesn't have.
vi.mock('../../lib/log', () => ({ logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } }))
const request = vi.mocked(apiRequest)

const draft = (mealIds: string[]): GroceryOrder => ({
  book_state: null,
  book_on: null,
  booking: null,
  book_issue: null,
  id: 'draft',
  number: 4,
  status: 'draft',
  created_by: { user_id: 'sam', name: 'Sam' },
  created_at: '2026-09-24T09:00:00Z',
  target_date: '2026-09-27',
  decide_by_date: '2026-09-26',
  reminders_start_date: null,
  earliest_delivery_date: '2026-09-25',
  delivery_date: null,
  delivery_by: null,
  next_delivery_date: null,
  reached_step: 'pick',
  jobs: [],
  picks: mealIds.map((meal_id) => ({ meal_id, servings: 4 })),
  lock: null,
  shopping_list: null,
  quote: null,
  best_window: null,
  fallback_window: null,
})

// The ordering session of the flow making the changes.
const session = 'phone'

let queryClient: QueryClient

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  registerOrderMutations(queryClient)
})

afterEach(() => request.mockReset())

const setNotForUs = (change: SetNotForUs) =>
  new MutationObserver<GroceryOrder, Error, SetNotForUs>(queryClient, {
    mutationKey: ['orders', 'set-not-for-us'],
  }).mutate(change)

describe('marking a suggestion "not for us"', () => {
  it('saves the rating, then shows the order as the server now has it', async () => {
    const after = draft(['soup'])
    request.mockImplementation(async (method) => (method === 'GET' ? after : undefined))
    await setNotForUs({ kind: 'not-for-us', orderId: 'draft', mealId: 'tacos', notForUs: true, session })

    expect(request.mock.calls).toEqual([
      ['PUT', '/v1/meals/tacos/rating', { rating: 'not_for_us' }, session],
      ['GET', '/v1/grocery-orders/draft'],
    ])
    expect(queryClient.getQueryData<GroceryOrder>(['grocery-order', 'draft'])).toEqual(after)
    expect(queryClient.getQueryData<NextOrder>(nextQuery.queryKey)?.draft).toEqual(after)
  })

  it("doesn't store an order someone placed meanwhile as the draft", async () => {
    const shownDraft = draft(['tacos'])
    queryClient.setQueryData<NextOrder>(nextQuery.queryKey, {
      number: 4,
      target_date: '2026-09-27',
      decide_by_date: '2026-09-26',
      reminders_start_date: null,
      earliest_delivery_date: '2026-09-25',
      draft: shownDraft,
    })
    request.mockImplementation(async (method) => (method === 'GET' ? { ...draft([]), status: 'placed' } : undefined))
    await setNotForUs({ kind: 'not-for-us', orderId: 'draft', mealId: 'tacos', notForUs: true, session })

    expect(queryClient.getQueryData<NextOrder>(nextQuery.queryKey)?.draft).toBe(shownDraft)
    expect(queryClient.getQueryState(nextQuery.queryKey)?.isInvalidated).toBe(true)
  })

  it('shows the mark on the meal page too', async () => {
    queryClient.setQueryData<Meal>(queryKeys.meal('tacos'), {
      id: 'tacos',
      name: 'Tacos',
      description: null,
      active_time_minutes: null,
      total_time_minutes: null,
      ingredient_count: 2,
      picture: null,
      rating: null,
      servings: 4,
      steps: [],
      ingredients: [],
    })
    request.mockImplementation(async (method) => (method === 'GET' ? draft([]) : undefined))
    await setNotForUs({ kind: 'not-for-us', orderId: 'draft', mealId: 'tacos', notForUs: true, session })

    expect(queryClient.getQueryData<Meal>(queryKeys.meal('tacos'))?.rating).toBe('not_for_us')
  })

  it('undoes it by clearing the rating', async () => {
    request.mockImplementation(async (method) => (method === 'GET' ? draft([]) : undefined))
    await setNotForUs({ kind: 'not-for-us', orderId: 'draft', mealId: 'tacos', notForUs: false, session })

    expect(request.mock.calls[0]).toEqual(['DELETE', '/v1/meals/tacos/rating'])
  })

  it('is saved after the changes before it, like a pick', async () => {
    const order: string[] = []
    request.mockImplementation(async (method, path) => {
      order.push(`${method} ${path}`)
      await new Promise((resolve) => setTimeout(resolve, method === 'PUT' && path.includes('/meals/soup') ? 20 : 0))
      return method === 'GET' || path.includes('grocery-orders') ? draft([]) : undefined
    })
    const pick = new MutationObserver<GroceryOrder, Error, SetMeal>(queryClient, {
      mutationKey: ['orders', 'set-meal'],
    }).mutate({ kind: 'meal', orderId: 'draft', mealId: 'soup', picked: true, servings: 4, session })
    await Promise.all([pick, setNotForUs({ kind: 'not-for-us', orderId: 'draft', mealId: 'tacos', notForUs: true, session })])

    expect(order).toEqual([
      'PUT /v1/grocery-orders/draft/meals/soup',
      'PUT /v1/meals/tacos/rating',
      'GET /v1/grocery-orders/draft',
    ])
  })
})

describe('opening the draft', () => {
  it('is saved before changes made while it opens, so its answer never replaces theirs', async () => {
    const calls: string[] = []
    const opened: GroceryOrder = {
      ...draft([]),
      jobs: [
        {
          id: 'job',
          kind: 'generate_meals',
          status: 'succeeded',
          error: null,
          requested_by: { user_id: 'sam', name: 'Sam' },
          created_at: '2026-09-24T09:00:00Z',
          meals: [],
        },
      ],
    }
    const picked = { ...opened, picks: [{ meal_id: 'soup', servings: 4 }] }
    request.mockImplementation(async (method, path) => {
      calls.push(`${method} ${path}`)
      // Opening is slow; the pick would finish first if it didn't wait.
      if (method === 'POST') await new Promise((resolve) => setTimeout(resolve, 20))
      return method === 'POST' ? opened : picked
    })
    const open = new MutationObserver<GroceryOrder, Error, OpenOrder>(queryClient, {
      mutationKey: ['orders', 'open'],
    }).mutate({ jobId: 'job', session })
    const pick = new MutationObserver<GroceryOrder, Error, SetMeal>(queryClient, {
      mutationKey: ['orders', 'set-meal'],
    }).mutate({ kind: 'meal', orderId: 'draft', mealId: 'soup', picked: true, servings: 4, session })
    await Promise.all([open, pick])

    expect(calls).toEqual(['POST /v1/grocery-orders', 'PUT /v1/grocery-orders/draft/meals/soup'])
    expect(queryClient.getQueryData<GroceryOrder>(queryKeys.order('draft'))?.picks).toEqual([{ meal_id: 'soup', servings: 4 }])
  })
})

describe('placing the order', () => {
  const place = (dates: PlacedDates) =>
    new MutationObserver<GroceryOrder, Error, PlaceOrder>(queryClient, { mutationKey: ['orders', 'place'] }).mutate({
      orderId: 'draft',
      dates,
      quoteId: 'quote-1',
      session,
    })
  const dates: PlacedDates = { delivery_date: '2026-09-27', delivery_by: '18:00:00', next_delivery_date: '2026-09-30' }

  it('sends the dates and time shown with it, and the total it was placed for, and keeps the placed order', async () => {
    request.mockResolvedValue({ ...draft(['soup']), ...dates, status: 'placed' })

    const placed = await place(dates)

    expect(request.mock.calls).toEqual([
      ['POST', '/v1/grocery-orders/draft/place', { ...dates, quote_id: 'quote-1' }, session],
    ])
    expect(placed.status).toBe('placed')
    expect(queryClient.getQueryData<GroceryOrder>(queryKeys.order('draft'))?.status).toBe('placed')
  })

  it('shows the order as it is when placing is refused', async () => {
    queryClient.setQueryData(queryKeys.order('draft'), draft(['soup']))
    request.mockRejectedValueOnce(new ApiError(409, 'Choose a delivery date from tomorrow on.'))

    await expect(place(dates)).rejects.toThrow('Choose a delivery date')

    expect(queryClient.getQueryState(queryKeys.order('draft'))?.isInvalidated).toBe(true)
  })
})

describe('the order lock', () => {
  const job = {
    id: 'job',
    kind: 'generate_meals' as const,
    status: 'succeeded' as const,
    error: null,
    requested_by: { user_id: 'sam', name: 'Sam' },
    created_at: '2026-09-24T09:00:00Z',
    meals: [],
  }
  const open = () =>
    new MutationObserver<GroceryOrder, Error, OpenOrder>(queryClient, { mutationKey: ['orders', 'open'] }).mutate({
      jobId: 'job',
      session,
    })
  const lock = (lockSession: string | null) => ({
    member: { user_id: lockSession === session ? 'me' : 'sam', name: 'Sam' },
    session: lockSession,
    since: '2026-09-24T09:00:00Z',
    step: 'pick' as const,
    expires_at: '2026-09-24T09:02:00Z',
  })

  it("opening a draft someone else is ordering doesn't start its first batch", async () => {
    const theirs = { ...draft([]), lock: lock(null) }
    request.mockResolvedValue(theirs)

    expect(await open()).toEqual(theirs)
    expect(request.mock.calls).toEqual([['POST', '/v1/grocery-orders', undefined, session]])
  })

  it('opening starts the first batch, and shows the order as it is if the lock was taken meanwhile', async () => {
    const ours = { ...draft([]), lock: lock(session) }
    const now = { ...draft([]), lock: lock(null) }
    request.mockImplementation(async (method, path) => {
      if (method === 'POST' && path.endsWith('/jobs')) {
        throw new ApiError(423, 'Someone else in your household is placing this order.')
      }
      return method === 'POST' ? ours : now
    })

    expect(await open()).toEqual(now)
    expect(request.mock.calls).toEqual([
      ['POST', '/v1/grocery-orders', undefined, session],
      ['POST', '/v1/grocery-orders/draft/jobs', { job_id: 'job' }, session],
      ['GET', '/v1/grocery-orders/draft'],
    ])
  })

  it('closing the flow releases the lock after the changes made before it', async () => {
    const closing = 'closing-phone'
    queryClient.setQueryData(queryKeys.order('draft'), { ...draft([]), lock: lock(closing) })
    const calls: string[] = []
    request.mockImplementation(async (method, path) => {
      calls.push(`${method} ${path}`)
      if (method === 'PUT') await new Promise((resolve) => setTimeout(resolve, 20))
      return method === 'PUT' ? { ...draft(['soup']), lock: lock(closing) } : undefined
    })
    const pick = new MutationObserver<GroceryOrder, Error, SetMeal>(queryClient, {
      mutationKey: ['orders', 'set-meal'],
    }).mutate({ kind: 'meal', orderId: 'draft', mealId: 'soup', picked: true, servings: 4, session: closing })
    closeFlowSession(queryClient, closing, 'draft')
    await pick
    await vi.waitFor(() => expect(calls).toHaveLength(2))

    expect(calls).toEqual(['PUT /v1/grocery-orders/draft/meals/soup', 'DELETE /v1/grocery-orders/draft/lock'])
  })

  it('closing the flow while it opens releases the lock the opening takes', async () => {
    const closing = 'opening-phone'
    const opened = { ...draft([]), jobs: [job], lock: lock(closing) }
    const calls: string[] = []
    let answer = () => {}
    request.mockImplementation(async (method, path) => {
      calls.push(`${method} ${path}`)
      if (method === 'POST') await new Promise<void>((resolve) => (answer = resolve))
      return method === 'POST' ? opened : undefined
    })
    const opening = new MutationObserver<GroceryOrder, Error, OpenOrder>(queryClient, {
      mutationKey: ['orders', 'open'],
    }).mutate({ jobId: 'job', session: closing })
    // Closed while the request is on its way, before the order is known.
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    closeFlowSession(queryClient, closing, undefined)
    answer()
    await opening
    await vi.waitFor(() => expect(calls).toHaveLength(2))

    expect(calls).toEqual(['POST /v1/grocery-orders', 'DELETE /v1/grocery-orders/draft/lock'])
  })

  it('a lock being taken as the flow closes is released after it, and none is taken after', async () => {
    const closing = 'taking-phone'
    const calls: string[] = []
    let answer = () => {}
    request.mockImplementation(async (method, path) => {
      calls.push(`${method} ${path}`)
      if (method === 'POST') await new Promise<void>((resolve) => (answer = resolve))
      return method === 'POST' ? lock(closing) : undefined
    })
    const taking = takeLock(queryClient, { orderId: 'draft', session: closing, step: 'pick' })
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    closeFlowSession(queryClient, closing, 'draft')
    answer()
    await taking
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    await expect(takeLock(queryClient, { orderId: 'draft', session: closing, step: 'pick' })).rejects.toBeInstanceOf(
      FlowClosed,
    )

    expect(calls).toEqual(['POST /v1/grocery-orders/draft/lock', 'DELETE /v1/grocery-orders/draft/lock'])
  })
})

describe('the shopping list', () => {
  it('starts after the changes before it, with the session, and shows the order as it is then', async () => {
    const calls: string[] = []
    request.mockImplementation(async (method, path) => {
      calls.push(`${method} ${path}`)
      if (method === 'PUT') await new Promise((resolve) => setTimeout(resolve, 20))
      return method === 'PUT' ? draft(['soup']) : { id: 'build', kind: 'build_list', status: 'pending' }
    })
    queryClient.setQueryData(queryKeys.order('draft'), draft([]))
    const servings = new MutationObserver<GroceryOrder, Error, SetServings>(queryClient, {
      mutationKey: ['orders', 'set-servings'],
    }).mutate({ kind: 'servings', orderId: 'draft', mealId: 'soup', servings: 6, session })
    const start = new MutationObserver<Job, Error, StartJob>(queryClient, {
      mutationKey: ['orders', 'start-list'],
    }).mutate({ orderId: 'draft', jobId: 'build', session })
    await Promise.all([servings, start])

    expect(calls).toEqual(['PUT /v1/grocery-orders/draft/meals/soup/servings', 'POST /v1/grocery-orders/draft/list'])
    expect(request.mock.calls[1]).toEqual(['POST', '/v1/grocery-orders/draft/list', { job_id: 'build' }, session])
    expect(queryClient.getQueryState(queryKeys.order('draft'))?.isInvalidated).toBe(true)
  })
})
