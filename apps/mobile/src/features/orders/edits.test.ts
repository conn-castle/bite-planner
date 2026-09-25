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
  earliest_delivery_date: '2026-09-26',
  delivery_date: '2026-09-29',
  next_delivery_date: '2026-10-02',
  jobs: [],
  meal_ids: ['tacos'],
}

describe('applyEdits', () => {
  it('shows the order as the server has it when nothing is being saved', () => {
    expect(applyEdits(order, [])).toBe(order)
  })

  it('shows picks and dates still being saved', () => {
    const shown = applyEdits(order, [
      { orderId: 'order-2', mealId: 'soup', picked: true },
      { orderId: 'order-2', mealId: 'tacos', picked: false },
      { orderId: 'order-2', dates: { delivery_date: '2026-09-30', next_delivery_date: null } },
    ])
    expect(shown.meal_ids).toEqual(['soup'])
    expect(shown.delivery_date).toBe('2026-09-30')
    expect(shown.next_delivery_date).toBeNull()
  })

  it('lets the latest change to a meal win, whatever came before', () => {
    const shown = applyEdits(order, [
      { orderId: 'order-2', mealId: 'soup', picked: true },
      { orderId: 'order-2', mealId: 'soup', picked: false },
      { orderId: 'order-2', mealId: 'soup', picked: true },
    ])
    expect(shown.meal_ids).toEqual(['tacos', 'soup'])
  })

  it('ignores changes to other orders', () => {
    const shown = applyEdits(order, [{ orderId: 'order-1', mealId: 'soup', picked: true }])
    expect(shown.meal_ids).toEqual(['tacos'])
  })
})

describe('isOrderEdit', () => {
  it('recognizes a pick and a change of dates', () => {
    expect(isOrderEdit({ orderId: 'o', mealId: 'm', picked: true })).toBe(true)
    expect(isOrderEdit({ orderId: 'o', dates: { delivery_date: '2026-09-30', next_delivery_date: null } })).toBe(true)
  })

  it('rejects anything else a pending mutation may hold', () => {
    expect(isOrderEdit(undefined)).toBe(false)
    expect(isOrderEdit({ orderId: 'o' })).toBe(false)
    expect(isOrderEdit({ orderId: 'o', jobId: 'j' })).toBe(false)
    expect(isOrderEdit({ orderId: 'o', mealId: 'm' })).toBe(false)
    expect(isOrderEdit({ orderId: 'o', dates: { delivery_date: 5 } })).toBe(false)
  })
})
