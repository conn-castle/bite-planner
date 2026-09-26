import type { GroceryOrder, OrderDates } from '../../lib/api/types'
import { isRecord } from '../../lib/isRecord'

/**
 * Changes to an order that are still being saved. The cached order is only
 * ever what the server sent; what the app shows is that order with these
 * changes applied over it, in the order they were made. A refresh can then
 * arrive at any time without losing them, and a change that fails simply
 * stops being applied.
 */

/** A pick, with the servings it starts at (the household's default) shown until it's saved; or an unpick. */
export type SetMeal =
  | { orderId: string; mealId: string; picked: true; servings: number }
  | { orderId: string; mealId: string; picked: false }

/** How many to make of one of the picks. */
export interface SetServings {
  orderId: string
  mealId: string
  servings: number
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

export type OrderEdit = SetMeal | SetServings | SetDates | SetNotForUs

const isDate = (value: unknown): value is string | null => value === null || typeof value === 'string'

const isCount = (value: unknown): value is number => typeof value === 'number' && Number.isInteger(value)

/** Whether a pending mutation's variables are a change to an order (they are stored untyped). */
export function isOrderEdit(value: unknown): value is OrderEdit {
  if (!isRecord(value) || typeof value.orderId !== 'string') return false
  if (typeof value.mealId === 'string') {
    if (typeof value.picked === 'boolean') return value.picked === false || isCount(value.servings)
    if (typeof value.notForUs === 'boolean') return true
    return isCount(value.servings)
  }
  const { dates } = value
  return isRecord(dates) && isDate(dates.delivery_date) && isDate(dates.next_delivery_date)
}

/** The order as it will be once every change is saved. */
export function applyEdits(order: GroceryOrder, edits: readonly OrderEdit[]): GroceryOrder {
  return edits
    .filter((edit) => edit.orderId === order.id)
    .reduce((shown, edit) => {
      if ('dates' in edit) return { ...shown, ...edit.dates }
      const others = shown.picks.filter((pick) => pick.meal_id !== edit.mealId)
      if ('picked' in edit) {
        // A meal marked "not for us" can't be picked (the server refuses it), so a
        // pick made while the mark is still being saved isn't shown either.
        const notForUs = shown.jobs.some((job) =>
          job.meals.some((meal) => meal.id === edit.mealId && meal.rating === 'not_for_us'),
        )
        if (!edit.picked || notForUs) return { ...shown, picks: others }
        // Picking it again keeps the servings it has.
        const picked = others.length < shown.picks.length
        return picked ? shown : { ...shown, picks: [...others, { meal_id: edit.mealId, servings: edit.servings }] }
      }
      if ('notForUs' in edit) {
        const rating = edit.notForUs ? 'not_for_us' : null
        return {
          ...shown,
          jobs: shown.jobs.map((job) => ({
            ...job,
            meals: job.meals.map((meal) => (meal.id === edit.mealId ? { ...meal, rating } : meal)),
          })),
          picks: edit.notForUs ? others : shown.picks,
        }
      }
      // Only a pick has servings (the server refuses the rest).
      return {
        ...shown,
        picks: shown.picks.map((pick) => (pick.meal_id === edit.mealId ? { ...pick, servings: edit.servings } : pick)),
      }
    }, order)
}
