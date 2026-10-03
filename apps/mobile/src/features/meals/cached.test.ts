import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import type { GroceryOrder, MealSummary, NextOrder, PlacedOrder } from '../../lib/api/types'
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
  rating: null,
})

const draft = (id: string, meals: MealSummary[]): GroceryOrder => ({
  book_state: null,
  book_on: null,
  booking: null,
  book_issue: null,
  id,
  number: 4,
  status: 'draft',
  created_by: { user_id: 'sam', name: 'Sam' },
  created_at: '2026-09-24T09:00:00Z',
  target_date: null,
  decide_by_date: null,
  reminders_start_date: null,
  earliest_delivery_date: '2026-09-25',
  delivery_date: null,
  delivery_by: null,
  next_delivery_date: null,
  reached_step: 'pick',
  jobs: [
    {
      id: 'job',
      kind: 'generate_meals',
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
  best_window: null,
  fallback_window: null,
})

const placed: PlacedOrder = {
  book_state: 'booked',
  book_on: '2026-09-20',
  booking: null,
  book_issue: null,
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
      draft: draft('other', [meal('soup')]),
    })

    expect(cachedMealSummary(queryClient, 'fajitas')?.name).toBe('fajitas')
    expect(cachedMealSummary(queryClient, 'tacos')?.name).toBe('tacos')
    expect(cachedMealSummary(queryClient, 'soup')?.name).toBe('soup')
    expect(cachedMealSummary(queryClient, 'unknown')).toBeUndefined()
  })
})
