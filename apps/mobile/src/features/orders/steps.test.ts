import { describe, expect, it } from 'vitest'
import type { GroceryOrder, Job } from '../../lib/api/types'
import { stepShown } from './steps'

const batch: Job = {
  id: 'batch',
  kind: 'generate_meals',
  status: 'succeeded',
  error: null,
  requested_by: { user_id: 'me', name: 'Nick' },
  created_at: '2026-09-24T09:00:00Z',
  meals: [{ id: 'tacos', name: 'Tacos', description: null, active_time_minutes: 20, total_time_minutes: 30, rating: null }],
}
const order = (fields: Partial<GroceryOrder>): GroceryOrder => ({
  id: 'draft',
  number: 4,
  status: 'draft',
  created_by: { user_id: 'me', name: 'Nick' },
  created_at: '2026-09-24T09:00:00Z',
  target_date: '2026-09-27',
  decide_by_date: '2026-09-26',
  reminders_start_date: null,
  earliest_delivery_date: '2026-09-25',
  delivery_date: null,
  next_delivery_date: null,
  jobs: [batch],
  picks: [{ meal_id: 'tacos', servings: 4 }],
  lock: null,
  shopping_list: null,
  ...fields,
})
const list = (current: boolean) => ({ job: { ...batch, id: 'build', kind: 'build_list' as const }, current, items: [] })

describe('stepShown', () => {
  it('shows the step the member is at when the order supports it', () => {
    const ready = order({ shopping_list: list(true) })
    for (const step of ['pick', 'servings', 'delivery', 'next', 'list', 'review'] as const) {
      expect(stepShown(ready, step)).toBe(step)
    }
  })

  it('goes back to picking when there is nothing picked', () => {
    expect(stepShown(order({ picks: [] }), 'review')).toBe('pick')
  })

  it('goes back to the delivery date when there is none (the first order)', () => {
    const first = order({ target_date: null, shopping_list: list(true) })
    expect(stepShown(first, 'next')).toBe('delivery')
    expect(stepShown(first, 'review')).toBe('delivery')
  })

  it("keeps the review for a list made for the picks now: the shopping list waits for it otherwise", () => {
    expect(stepShown(order({ shopping_list: null }), 'review')).toBe('list')
    expect(stepShown(order({ shopping_list: list(false) }), 'review')).toBe('list')
  })
})
