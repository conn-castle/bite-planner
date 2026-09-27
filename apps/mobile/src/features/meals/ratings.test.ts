import { MutationObserver, QueryClient } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiRequest } from '../../lib/api/client'
import { ApiError } from '../../lib/api/errors'
import type { Meal, MealSummary, PlacedOrder } from '../../lib/api/types'
import { placedQuery } from '../orders/orders'
import { mealQuery } from './meals'
import { type RateMeal, registerRatingMutations } from './ratings'

vi.mock('../../lib/api/client', () => ({ apiRequest: vi.fn() }))
// The app's logger stores its entries on the device, which a test doesn't have.
vi.mock('../../lib/log', () => ({ logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } }))
const request = vi.mocked(apiRequest)

const summary = (id: string): MealSummary => ({
  id,
  name: id,
  description: null,
  active_time_minutes: null,
  total_time_minutes: null,
  rating: null,
})

const placed: PlacedOrder = {
  id: 'order-3',
  number: 3,
  placed_at: '2026-09-18T09:00:00Z',
  placed_by: { user_id: 'sam', name: 'Sam' },
  delivery_date: '2026-09-20',
  next_delivery_date: '2026-09-25',
  shopping_list: [],
  meals: [summary('fajitas'), summary('curry')].map((meal) => ({ ...meal, servings: 4 })),
}

let queryClient: QueryClient

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  registerRatingMutations(queryClient)
  queryClient.setQueryData(placedQuery('2026-09-14').queryKey, [placed])
  queryClient.setQueryData(placedQuery().queryKey, [placed])
  queryClient.setQueryData<Meal>(mealQuery('fajitas').queryKey, {
    ...summary('fajitas'),
    servings: 4,
    steps: [],
    ingredients: [],
  })
})

afterEach(() => request.mockReset())

const rate = (change: RateMeal) =>
  new MutationObserver<void, Error, RateMeal>(queryClient, { mutationKey: ['meals', 'rate'] }).mutate(change)

const placedMeals = (coveringFrom?: string) =>
  queryClient.getQueryData<PlacedOrder[]>(placedQuery(coveringFrom).queryKey)?.[0]?.meals.map((meal) => meal.rating)

describe('rating a meal', () => {
  it('saves it and shows it wherever the meal is cached', async () => {
    request.mockResolvedValue(undefined)
    await rate({ mealId: 'fajitas', rating: 'favorite' })

    expect(request).toHaveBeenCalledWith('PUT', '/v1/meals/fajitas/rating', { rating: 'favorite' })
    expect(placedMeals()).toEqual(['favorite', null])
    expect(placedMeals('2026-09-14')).toEqual(['favorite', null])
    expect(queryClient.getQueryData<Meal>(mealQuery('fajitas').queryKey)?.rating).toBe('favorite')
  })

  it('clears it with a delete', async () => {
    request.mockResolvedValue(undefined)
    await rate({ mealId: 'fajitas', rating: 'up' })
    await rate({ mealId: 'fajitas', rating: null })

    expect(request).toHaveBeenLastCalledWith('DELETE', '/v1/meals/fajitas/rating')
    expect(placedMeals()).toEqual([null, null])
  })

  it('keeps the last of several quick taps, sent in the order they were made', async () => {
    request.mockResolvedValue(undefined)
    await Promise.all([
      rate({ mealId: 'fajitas', rating: 'up' }),
      rate({ mealId: 'fajitas', rating: 'favorite' }),
      rate({ mealId: 'fajitas', rating: 'down' }),
    ])

    expect(request.mock.calls.map((call) => call[2])).toEqual([
      { rating: 'up' },
      { rating: 'favorite' },
      { rating: 'down' },
    ])
    expect(placedMeals()).toEqual(['down', null])
  })

  it("leaves the caches alone when it's refused, and refetches them", async () => {
    request.mockRejectedValue(new ApiError(404, 'No such meal.'))
    await expect(rate({ mealId: 'fajitas', rating: 'down' })).rejects.toThrow('No such meal.')

    expect(placedMeals()).toEqual([null, null])
    expect(queryClient.getQueryState(placedQuery().queryKey)?.isInvalidated).toBe(true)
  })
})
