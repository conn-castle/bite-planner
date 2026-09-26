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

/**
 * The ✕ on a suggestion: "don't show meals like this" (the household's
 * `not_for_us` rating), which also takes it out of the picks; or undoing it.
 */
export interface SetNotForUs {
  orderId: string
  mealId: string
  notForUs: boolean
}

export type OrderEdit = SetMeal | SetDates | SetNotForUs

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null

const isDate = (value: unknown): value is string | null => value === null || typeof value === 'string'

/** Whether a pending mutation's variables are a change to an order (they are stored untyped). */
export function isOrderEdit(value: unknown): value is OrderEdit {
  if (!isRecord(value) || typeof value.orderId !== 'string') return false
  if (typeof value.mealId === 'string' && typeof value.picked === 'boolean') return true
  if (typeof value.mealId === 'string' && typeof value.notForUs === 'boolean') return true
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
      if ('picked' in edit) {
        // A meal marked "not for us" can't be picked (the server refuses it), so a
        // pick made while the mark is still being saved isn't shown either.
        const notForUs = shown.jobs.some((job) =>
          job.meals.some((meal) => meal.id === edit.mealId && meal.rating === 'not_for_us'),
        )
        return { ...shown, meal_ids: edit.picked && !notForUs ? [...others, edit.mealId] : others }
      }
      const rating = edit.notForUs ? 'not_for_us' : null
      return {
        ...shown,
        jobs: shown.jobs.map((job) => ({
          ...job,
          meals: job.meals.map((meal) => (meal.id === edit.mealId ? { ...meal, rating } : meal)),
        })),
        meal_ids: edit.notForUs ? others : shown.meal_ids,
      }
    }, order)
}
