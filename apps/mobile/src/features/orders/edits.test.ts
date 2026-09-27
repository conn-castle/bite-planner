import { describe, expect, it } from 'vitest'
import type { GroceryOrder } from '../../lib/api/types'
import { applyEdits, isOrderEdit } from './edits'

const order: GroceryOrder = {
  id: 'order-2',
  number: 2,
  status: 'draft',
  created_by: { user_id: 'sam', name: 'Sam' },
  created_at: '2026-09-25T12:00:00Z',
  target_date: '2026-09-29',
  decide_by_date: '2026-09-28',
  reminders_start_date: null,
  earliest_delivery_date: '2026-09-26',
  delivery_date: '2026-09-29',
  next_delivery_date: '2026-10-02',
  jobs: [],
  picks: [{ meal_id: 'tacos', servings: 4 }],
  lock: null,
  shopping_list: null,
}

const picked = (shown: GroceryOrder) => shown.picks.map((pick) => pick.meal_id)

describe('applyEdits', () => {
  it('shows the order as the server has it when nothing is being saved', () => {
    expect(applyEdits(order, [])).toBe(order)
  })

  it('shows picks and dates still being saved', () => {
    const shown = applyEdits(order, [
      { session: 'phone', orderId: 'order-2', mealId: 'soup', picked: true, servings: 4 },
      { session: 'phone', orderId: 'order-2', mealId: 'tacos', picked: false },
      { session: 'phone', orderId: 'order-2', dates: { delivery_date: '2026-09-30', next_delivery_date: null } },
    ])
    expect(picked(shown)).toEqual(['soup'])
    expect(shown.delivery_date).toBe('2026-09-30')
    expect(shown.next_delivery_date).toBeNull()
  })

  it('lets the latest change to a meal win, whatever came before', () => {
    const shown = applyEdits(order, [
      { session: 'phone', orderId: 'order-2', mealId: 'soup', picked: true, servings: 4 },
      { session: 'phone', orderId: 'order-2', mealId: 'soup', picked: false },
      { session: 'phone', orderId: 'order-2', mealId: 'soup', picked: true, servings: 4 },
    ])
    expect(picked(shown)).toEqual(['tacos', 'soup'])
  })

  it('shows servings still being saved, and a new pick at the servings it starts at', () => {
    const shown = applyEdits(order, [
      { session: 'phone', orderId: 'order-2', mealId: 'tacos', servings: 6 },
      { session: 'phone', orderId: 'order-2', mealId: 'soup', picked: true, servings: 2 },
    ])
    expect(shown.picks).toEqual([
      { meal_id: 'tacos', servings: 6 },
      { meal_id: 'soup', servings: 2 },
    ])
  })

  it("keeps a pick's servings when it's picked again, and gives a meal that isn't picked none", () => {
    const shown = applyEdits(order, [
      { session: 'phone', orderId: 'order-2', mealId: 'tacos', servings: 6 },
      { session: 'phone', orderId: 'order-2', mealId: 'tacos', picked: true, servings: 4 },
      // Refused by the server (not picked), so not shown either.
      { session: 'phone', orderId: 'order-2', mealId: 'soup', servings: 3 },
    ])
    expect(shown.picks).toEqual([{ meal_id: 'tacos', servings: 6 }])
  })

  it('drops servings still being saved when the meal is unpicked', () => {
    const shown = applyEdits(order, [
      { session: 'phone', orderId: 'order-2', mealId: 'tacos', servings: 6 },
      { session: 'phone', orderId: 'order-2', mealId: 'tacos', picked: false },
    ])
    expect(shown.picks).toEqual([])
  })

  it('ignores changes to other orders', () => {
    const shown = applyEdits(order, [{ session: 'phone', orderId: 'order-1', mealId: 'soup', picked: true, servings: 4 }])
    expect(picked(shown)).toEqual(['tacos'])
  })

  it('shows a "not for us" mark still being saved, which takes the meal out of the picks', () => {
    const withIdeas: GroceryOrder = {
      ...order,
      jobs: [
        {
          id: 'job',
          kind: 'generate_meals',
          status: 'succeeded',
          error: null,
          requested_by: { user_id: 'sam', name: 'Sam' },
          created_at: '2026-09-25T12:00:00Z',
          meals: ['tacos', 'soup'].map((id) => ({
            id,
            name: id,
            description: null,
            active_time_minutes: null,
            total_time_minutes: null,
            rating: null,
          })),
        },
      ],
    }
    const marked = applyEdits(withIdeas, [{ session: 'phone', orderId: 'order-2', mealId: 'tacos', notForUs: true }])
    expect(picked(marked)).toEqual([])
    expect(marked.jobs[0]?.meals.map((meal) => meal.rating)).toEqual(['not_for_us', null])

    // A pick made while the mark is still being saved isn't shown: the server refuses it.
    const pickedAfter = applyEdits(withIdeas, [
      { session: 'phone', orderId: 'order-2', mealId: 'soup', notForUs: true },
      { session: 'phone', orderId: 'order-2', mealId: 'soup', picked: true, servings: 4 },
    ])
    expect(picked(pickedAfter)).toEqual(['tacos'])

    // Undoing it leaves the meal unpicked, as the server does.
    const undone = applyEdits(marked, [{ session: 'phone', orderId: 'order-2', mealId: 'tacos', notForUs: false }])
    expect(picked(undone)).toEqual([])
    expect(undone.jobs[0]?.meals.map((meal) => meal.rating)).toEqual([null, null])
  })
})

describe('changes to the shopping list', () => {
  const listed: GroceryOrder = {
    ...order,
    shopping_list: {
      job: {
        id: 'build',
        kind: 'build_list',
        status: 'succeeded',
        error: null,
        requested_by: { user_id: 'sam', name: 'Sam' },
        created_at: '2026-09-25T12:00:00Z',
        meals: [],
      },
      current: true,
      items: [
        { id: 'onions', name: 'onions', quantity: 2, recipe_quantity: 2, unit: null, aisle: 'produce', meal_ids: [], on_hand: false, removed: false },
        { id: 'beef', name: 'ground beef', quantity: 1, recipe_quantity: 1, unit: 'lb', aisle: 'meat_seafood', meal_ids: [], on_hand: false, removed: false },
      ],
    },
  }

  it('shows items taken off and amounts changed while they are saved', () => {
    const shown = applyEdits(listed, [
      { session: 'phone', orderId: 'order-2', itemId: 'onions', removed: true },
      { session: 'phone', orderId: 'order-2', itemId: 'beef', quantity: 1.5 },
    ])
    expect(shown.shopping_list?.items.map((item) => [item.id, item.removed, item.quantity])).toEqual([
      ['onions', true, 2],
      ['beef', false, 1.5],
    ])
  })

  it('is recognized as a change to the order', () => {
    expect(isOrderEdit({ session: 's', orderId: 'o', itemId: 'i', removed: true })).toBe(true)
    expect(isOrderEdit({ session: 's', orderId: 'o', itemId: 'i', quantity: 'lots' })).toBe(false)
  })
})

describe('isOrderEdit', () => {
  it('recognizes a pick, an unpick, servings, a "not for us" mark, and a change of dates', () => {
    expect(isOrderEdit({ session: 's', orderId: 'o', mealId: 'm', picked: true, servings: 4 })).toBe(true)
    expect(isOrderEdit({ session: 's', orderId: 'o', mealId: 'm', picked: false })).toBe(true)
    expect(isOrderEdit({ session: 's', orderId: 'o', mealId: 'm', servings: 6 })).toBe(true)
    expect(isOrderEdit({ session: 's', orderId: 'o', mealId: 'm', notForUs: false })).toBe(true)
    expect(isOrderEdit({ session: 's', orderId: 'o', dates: { delivery_date: '2026-09-30', next_delivery_date: null } })).toBe(true)
  })

  it('rejects anything else a pending mutation may hold', () => {
    expect(isOrderEdit(undefined)).toBe(false)
    expect(isOrderEdit({ orderId: 'o' })).toBe(false)
    // Made outside an ordering session (e.g. saved by an older version of the app).
    expect(isOrderEdit({ orderId: 'o', mealId: 'm', picked: false })).toBe(false)
    expect(isOrderEdit({ orderId: 'o', jobId: 'j' })).toBe(false)
    expect(isOrderEdit({ orderId: 'o', mealId: 'm' })).toBe(false)
    expect(isOrderEdit({ orderId: 'o', mealId: 'm', picked: true })).toBe(false)
    expect(isOrderEdit({ orderId: 'o', mealId: 'm', servings: 'six' })).toBe(false)
    expect(isOrderEdit({ orderId: 'o', dates: { delivery_date: 5 } })).toBe(false)
  })
})
