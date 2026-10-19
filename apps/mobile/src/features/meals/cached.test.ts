import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import type { GroceryOrder, Meal, MealSummary, NextOrder, PlacedOrder } from '../../lib/api/types'
import { queryKeys } from '../../lib/query/keys'
import { cachedMealSummary } from './cached'

// The API client signs in through the browser, which a test doesn't have; nothing here calls it.
vi.mock('../../lib/api/client', () => ({ apiRequest: vi.fn() }))

const meal = (id: string): MealSummary => ({
  id,
  name: id,
  description: null,
  active_time_minutes: null,
  total_time_minutes: null,
  revision: 0, ingredient_count: 2,
  picture: null,
  rating: null,
})

const draft = (id: string, meals: MealSummary[]): GroceryOrder => ({
  book_state: null,
  book_on: null,
  booking: null,
  book_issue: null, can_confirm_absent: false,
  id,
  number: 4,
  status: 'draft',
  created_by: { user_id: 'sam', name: 'Sam' },
  created_at: '2026-09-24T09:00:00Z',
  target_date: null,
  decide_by_date: null,
  reminders_start_date: null,
  earliest_delivery_date: '2026-09-25',
  delivery_time_zone: 'America/New_York',
  delivery_date: null,
  delivery_by: null,
  next_delivery_date: null,
  recipe_changes: [], recipes_editable: true, reached_step: 'pick',
  jobs: [
    {
      id: 'job',
      meal_id: null, started_at: null, finished_at: null, kind: 'generate_meals',
      status: 'succeeded',
      error: null,
      requested_by: { user_id: 'sam', name: 'Sam' },
      created_at: '2026-09-24T09:00:00Z',
      meals,
    },
  ],
  picks: [],
  lock: null,
  shopping_list: null,
  quote: null,

})

const placed: PlacedOrder = {
  book_state: 'booked',
  book_on: '2026-09-20',
  booking: null,
  book_issue: null, can_confirm_absent: false,
  id: 'order-3',
  number: 3,
  placed_at: '2026-09-18T09:00:00Z',
  placed_by: { user_id: 'sam', name: 'Sam' },
  delivery_date: '2026-09-20',
  delivery_by: null,
  next_delivery_date: '2026-09-25',
  shopping_list: [],
  meals: [{ ...meal('fajitas'), servings: 4 }],
}

describe('cachedMealSummary', () => {
  it('finds a meal in any list already loaded: placed orders, an order, or the next order', () => {
    const queryClient = new QueryClient()
    queryClient.setQueryData(queryKeys.placedOrdersFrom(), [placed])
    queryClient.setQueryData(queryKeys.order('draft'), draft('draft', [meal('tacos')]))
    queryClient.setQueryData<NextOrder>(queryKeys.nextOrder, {
      number: 5,
      target_date: null,
      decide_by_date: null,
      reminders_start_date: null,
      earliest_delivery_date: '2026-09-25',
      delivery_time_zone: 'America/New_York',
      draft: draft('other', [meal('soup')]),
    })

    expect(cachedMealSummary(queryClient, 'fajitas')?.name).toBe('fajitas')
    expect(cachedMealSummary(queryClient, 'tacos')?.name).toBe('tacos')
    expect(cachedMealSummary(queryClient, 'soup')?.name).toBe('soup')
    expect(cachedMealSummary(queryClient, 'unknown')).toBeUndefined()
  })
})

it('scopes recipes to build status, source, target, revision, and placed build identity', async () => {
  const { mealQuery, placedMealPath } = await import('./meals')
  const { recipeCacheContext } = await import('./servings')
  const pending: GroceryOrder = { ...draft('draft', [meal('beans')]), picks: [{ meal_id: 'beans', servings: 5 }],
    shopping_list: { job: { ...draft('draft', []).jobs[0], id: 'build-1', kind: 'build_list', status: 'running' },
      source_current: true, current: false, shop: null, shop_current: false, address_problem: null, store: 'Wegmans', items: [] } }
  const done: GroceryOrder = { ...pending, shopping_list: pending.shopping_list ? { ...pending.shopping_list,
    job: { ...pending.shopping_list.job, status: 'succeeded' }, current: true } : null }
  const replaced: GroceryOrder = { ...done, shopping_list: done.shopping_list ? { ...done.shopping_list,
    job: { ...done.shopping_list.job, id: 'build-2' } } : null }
  const changed: GroceryOrder = { ...done, picks: [{ meal_id: 'beans', servings: 6 }] }
  const keys = [pending, done, replaced, changed].map((order) => mealQuery('beans', recipeCacheContext(order, 'beans', false)).queryKey)
  expect(new Set(keys.map((key) => JSON.stringify(key))).size).toBe(4)
  expect(mealQuery('beans', recipeCacheContext(undefined, 'beans', true, 'build-1')).queryKey)
    .not.toEqual(mealQuery('beans', recipeCacheContext(undefined, 'beans', true, 'build-2')).queryKey)
  expect(placedMealPath('/history', { ...meal('beans'), servings: 5, prepared_build_id: 'build-2' }))
    .toBe('/history/meals/beans?servings=5&build=build-2')
  const client: QueryClient = new QueryClient()
  const original: Meal = { ...meal('beans'), servings: 2, ingredients: [], steps: [], prepared: null }
  client.setQueryData(keys[0], original)
  expect(client.getQueryData(keys[1])).toBeUndefined()
  client.setQueryData(keys[1], { ...original, prepared: { build_id: 'build-1', base_revision: 0, servings: 5, legacy_scaled: false, ingredients: [], steps: [] } })
  expect(client.getQueryData(keys[2])).toBeUndefined()
})

it.each(['draft', 'placed'] as const)('shares an in-flight prefetch with page observation and fetches a changed source afresh (%s)', async (kind) => {
  const { apiRequest } = await import('../../lib/api/client')
  const { QueryObserver } = await import('@tanstack/react-query')
  const { mealQuery } = await import('./meals')
  const { recipeCacheContext } = await import('./servings')
  const base: Meal = { ...meal('beans'), servings: 2, ingredients: [
    { name: 'beans', quantity: .75, unit: 'can', preparation: null }],
    steps: [{ text: 'Heat ¾ can.', ingredients: [0], timer_minutes: 7 }], prepared: null }
  const ready: Meal = { ...base, prepared: { build_id: 'build-2', base_revision: 0, servings: 5,
    legacy_scaled: false, ingredients: [{ ...base.ingredients[0], quantity: 2 }],
    steps: [{ ...base.steps[0], text: 'Heat 2 cans.' }] } }
  const originalOrder: GroceryOrder = { ...draft('draft', [base]), picks: [{ meal_id: base.id, servings: 5 }] }
  const builtOrder: GroceryOrder = { ...originalOrder, shopping_list: {
    job: { ...originalOrder.jobs[0], id: 'build-2', kind: 'build_list', status: 'succeeded' },
    source_current: true, current: true, shop: null, shop_current: false, address_problem: null, store: 'Wegmans', items: [] } }
  const originalContext: string | undefined = recipeCacheContext(kind === 'draft' ? originalOrder : undefined, base.id, kind === 'placed', 'build-1')
  const freshContext: string | undefined = recipeCacheContext(kind === 'draft' ? builtOrder : undefined, base.id, kind === 'placed', 'build-2')
  const client: QueryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 30_000, retry: false } } })
  // An old unscoped prepared entry must never seed a contextual page.
  client.setQueryData(queryKeys.meal(base.id), { ...ready, name: 'Wrong old cached result' })
  let release: ((value: Meal) => void) | undefined
  const response: Promise<Meal> = new Promise((resolve) => { release = resolve })
  vi.mocked(apiRequest).mockClear().mockReturnValueOnce(response).mockResolvedValueOnce(ready)
  const prefetched: Promise<void> = client.prefetchQuery(mealQuery(base.id, originalContext))
  const page = new QueryObserver(client, mealQuery(base.id, originalContext))
  const unmount = page.subscribe(() => {})
  expect(page.getCurrentResult().data).toBeUndefined()
  expect(apiRequest).toHaveBeenCalledTimes(1)
  if (!release) throw new Error('The recipe request did not start')
  release(base)
  await prefetched
  expect(page.getCurrentResult().data).toEqual(base)
  expect(apiRequest).toHaveBeenCalledTimes(1)
  unmount()
  const changedPage = new QueryObserver(client, mealQuery(base.id, freshContext))
  expect(changedPage.getCurrentResult().data).toBeUndefined()
  const unmountChanged = changedPage.subscribe(() => {})
  await client.fetchQuery(mealQuery(base.id, freshContext))
  expect(apiRequest).toHaveBeenCalledTimes(2)
  expect(changedPage.getCurrentResult().data?.prepared?.ingredients[0].quantity).toBe(2)
  expect(changedPage.getCurrentResult().data?.prepared?.build_id).toBe('build-2')
  expect(client.getQueryData(mealQuery(base.id, originalContext).queryKey)).toEqual(base)
  unmountChanged()
  client.clear()
})
