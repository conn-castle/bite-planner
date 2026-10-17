import { describe, expect, it } from 'vitest'
import type { GroceryOrder, Job, ShoppingItem } from '../../lib/api/types'
import { stepShown } from './steps'

const batch: Job = {
  id: 'batch',
  meal_id: null, started_at: null, finished_at: null, kind: 'generate_meals',
  status: 'succeeded',
  error: null,
  requested_by: { user_id: 'me', name: 'Nick' },
  created_at: '2026-09-24T09:00:00Z',
  meals: [{ id: 'tacos', name: 'Tacos', description: null, active_time_minutes: 20, total_time_minutes: 30, revision: 0, ingredient_count: 2, rating: null, picture: null }],
}
const order = (fields: Partial<GroceryOrder>): GroceryOrder => ({
  book_state: null,
  book_on: null,
  booking: null,
  book_issue: null,
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
  delivery_by: null,
  next_delivery_date: null,
  recipe_changes: [], recipes_editable: true, reached_step: 'pick',
  jobs: [batch],
  picks: [{ meal_id: 'tacos', servings: 4 }],
  lock: null,
  shopping_list: null,
  quote: null,
  best_window: null,
  fallback_window: null,
  ...fields,
})
const onions: ShoppingItem = {
  id: 'onions',
  name: 'yellow onions',
  quantity: 2,
  recipe_quantity: 2,
  unit: null,
  aisle: 'produce',
  meal_ids: ['tacos'],
  on_hand: false,
  removed: false,
  product: {
    product_id: '17499438',
    name: 'Wegmans Onions, Yellow',
    size: '2 lb',
    price_cents: 229,
    price_per_lb_cents: null,
    estimated_lb: null,
    image_url: null,
    bought_before: true,
    count: 1,
  },
  alternatives: [],
  more_alternatives_query: null,
  more_alternatives: [],
  no_alternatives_reason: null,
  not_found: false,
  skippable_reason: null,
}
const list = (current: boolean) => ({
  job: { ...batch, id: 'build', kind: 'build_list' as const },
  current,
  shop: { ...batch, id: 'shop', kind: 'shop' as const },
  shop_current: true,
  address_problem: null,
  store: 'Wegmans',
  items: [onions],
})

describe('stepShown', () => {
  it('shows the step the member is at when the order supports it', () => {
    const ready = order({ shopping_list: list(true) })
    for (const step of ['pick', 'servings', 'list', 'delivery', 'next', 'review'] as const) {
      expect(stepShown(ready, step)).toBe(step)
    }
  })

  it("opens where the order's flow got to, and shows the step the member goes to after", () => {
    const reviewed = order({ shopping_list: list(true), reached_step: 'review' })
    expect(stepShown(reviewed, undefined)).toBe('review')
    expect(stepShown(reviewed, 'servings')).toBe('servings')
    expect(stepShown(order({ shopping_list: list(true) }), undefined)).toBe('pick')
  })

  it('opens as far as the order allows when it no longer supports the step its flow got to', () => {
    // The picks changed after the list was made: their list is next.
    expect(stepShown(order({ shopping_list: list(false), reached_step: 'review' }), undefined)).toBe('list')
    expect(stepShown(order({ picks: [], reached_step: 'review' }), undefined)).toBe('pick')
    // Everything on the list was taken off since.
    const emptied = { ...list(true), items: [{ ...onions, removed: true }] }
    expect(stepShown(order({ shopping_list: emptied, reached_step: 'review' }), undefined)).toBe('list')
  })

  it('goes back to picking when there is nothing picked', () => {
    expect(stepShown(order({ picks: [] }), 'review')).toBe('pick')
  })

  it('goes back to the delivery date when there is none (the first order)', () => {
    const first = order({ target_date: null, shopping_list: list(true) })
    expect(stepShown(first, 'next')).toBe('delivery')
    expect(stepShown(first, 'review')).toBe('delivery')
  })

  it('keeps the steps after the list for a list made for the picks now and shopped: the list waits otherwise', () => {
    for (const step of ['delivery', 'next', 'review'] as const) {
      expect(stepShown(order({ shopping_list: null }), step)).toBe('list')
      expect(stepShown(order({ shopping_list: list(false) }), step)).toBe('list')
      expect(stepShown(order({ shopping_list: { ...list(true), shop: null } }), step)).toBe('list')
    }
  })
})
