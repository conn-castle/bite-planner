import { MutationObserver, QueryClient } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiRequest } from '../../lib/api/client'
import type { GroceryOrder, Meal, NextOrder } from '../../lib/api/types'
import { queryKeys } from '../../lib/query/keys'
import type { SetNotForUs } from './edits'
import { nextQuery, registerOrderMutations } from './orders'

vi.mock('../../lib/api/client', () => ({ apiRequest: vi.fn() }))
// The app's logger stores its entries on the device, which a test doesn't have.
vi.mock('../../lib/log', () => ({ logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } }))
const request = vi.mocked(apiRequest)

const draft = (mealIds: string[]): GroceryOrder => ({
  id: 'draft',
  number: 4,
  status: 'draft',
  created_by: { user_id: 'sam', name: 'Sam' },
  created_at: '2026-09-24T09:00:00Z',
  target_date: '2026-09-27',
  decide_by_date: '2026-09-26',
  earliest_delivery_date: '2026-09-25',
  delivery_date: null,
  next_delivery_date: null,
  jobs: [],
  meal_ids: mealIds,
})

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
    await setNotForUs({ orderId: 'draft', mealId: 'tacos', notForUs: true })

    expect(request.mock.calls).toEqual([
      ['PUT', '/v1/meals/tacos/rating', { rating: 'not_for_us' }],
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
      earliest_delivery_date: '2026-09-25',
      draft: shownDraft,
    })
    request.mockImplementation(async (method) => (method === 'GET' ? { ...draft([]), status: 'placed' } : undefined))
    await setNotForUs({ orderId: 'draft', mealId: 'tacos', notForUs: true })

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
      rating: null,
      servings: 4,
      steps: [],
      ingredients: [],
    })
    request.mockImplementation(async (method) => (method === 'GET' ? draft([]) : undefined))
    await setNotForUs({ orderId: 'draft', mealId: 'tacos', notForUs: true })

    expect(queryClient.getQueryData<Meal>(queryKeys.meal('tacos'))?.rating).toBe('not_for_us')
  })

  it('undoes it by clearing the rating', async () => {
    request.mockImplementation(async (method) => (method === 'GET' ? draft([]) : undefined))
    await setNotForUs({ orderId: 'draft', mealId: 'tacos', notForUs: false })

    expect(request.mock.calls[0]).toEqual(['DELETE', '/v1/meals/tacos/rating'])
  })

  it('is saved after the changes before it, like a pick', async () => {
    const order: string[] = []
    request.mockImplementation(async (method, path) => {
      order.push(`${method} ${path}`)
      await new Promise((resolve) => setTimeout(resolve, method === 'PUT' && path.includes('/meals/soup') ? 20 : 0))
      return method === 'GET' || path.includes('grocery-orders') ? draft([]) : undefined
    })
    const pick = new MutationObserver<GroceryOrder, Error, { orderId: string; mealId: string; picked: boolean }>(
      queryClient,
      { mutationKey: ['orders', 'set-meal'] },
    ).mutate({ orderId: 'draft', mealId: 'soup', picked: true })
    await Promise.all([pick, setNotForUs({ orderId: 'draft', mealId: 'tacos', notForUs: true })])

    expect(order).toEqual([
      'PUT /v1/grocery-orders/draft/meals/soup',
      'PUT /v1/meals/tacos/rating',
      'GET /v1/grocery-orders/draft',
    ])
  })
})
