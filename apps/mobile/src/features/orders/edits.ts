import type { GroceryOrder, OrderDates } from '../../lib/api/types'

/**
 * Changes to an order that are still being saved. The cached order is only
 * ever what the server sent; what the app shows is that order with these
 * changes applied over it, in the order they were made. A refresh can then
 * arrive at any time without losing them, and a change that fails simply
 * stops being applied.
 */

export interface SetMeal {
  orderId: string
  mealId: string
  picked: boolean
}

export interface SetDates {
  orderId: string
  dates: OrderDates
}

export type OrderEdit = SetMeal | SetDates

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null

const isDate = (value: unknown): value is string | null => value === null || typeof value === 'string'

/** Whether a pending mutation's variables are a change to an order (they are stored untyped). */
export function isOrderEdit(value: unknown): value is OrderEdit {
  if (!isRecord(value) || typeof value.orderId !== 'string') return false
  if (typeof value.mealId === 'string' && typeof value.picked === 'boolean') return true
  const { dates } = value
  return isRecord(dates) && isDate(dates.delivery_date) && isDate(dates.next_delivery_date)
}

/** The order as it will be once every change is saved. */
export function applyEdits(order: GroceryOrder, edits: readonly OrderEdit[]): GroceryOrder {
  return edits
    .filter((edit) => edit.orderId === order.id)
    .reduce((shown, edit) => {
      if ('dates' in edit) return { ...shown, ...edit.dates }
      const others = shown.meal_ids.filter((id) => id !== edit.mealId)
      return { ...shown, meal_ids: edit.picked ? [...others, edit.mealId] : others }
    }, order)
}
