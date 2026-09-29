import type { GroceryOrder, OrderDates, ShoppingItem } from '../../lib/api/types'
import { isRecord } from '../../lib/isRecord'

/**
 * Changes to an order that are still being saved. The cached order is only
 * ever what the server sent; what the app shows is that order with these
 * changes applied over it, in the order they were made. A refresh can then
 * arrive at any time without losing them, and a change that fails simply
 * stops being applied.
 */

/**
 * Made in the order flow: the ordering session it comes from (see lock.ts),
 * sent with it. Saved with the change, so one sent after a relaunch counts only
 * while that session still has the order's lock.
 */
export interface InSession {
  session: string
}

/** A pick, with the servings it starts at (the household's default) shown until it's saved; or an unpick. */
export type SetMeal = InSession &
  ({ orderId: string; mealId: string; picked: true; servings: number } | { orderId: string; mealId: string; picked: false })

/** How many to make of one of the picks. */
export interface SetServings extends InSession {
  orderId: string
  mealId: string
  servings: number
}

export interface SetDates extends InSession {
  orderId: string
  dates: OrderDates
}

/**
 * The ✕ on a suggestion: "don't show meals like this" (the household's
 * `not_for_us` rating), which also takes it out of the picks; or undoing it.
 */
export interface SetNotForUs extends InSession {
  orderId: string
  mealId: string
  notForUs: boolean
}

/**
 * A shopping list item taken off the list (or put back; for a product, not
 * bought), how much of it to buy, how many of its product, or switched to one of
 * its alternatives (its product id).
 */
export interface SetItem extends InSession {
  orderId: string
  itemId: string
  removed?: boolean
  quantity?: number
  count?: number
  alternative?: string
}

export type OrderEdit = SetMeal | SetServings | SetDates | SetNotForUs | SetItem

const isDate = (value: unknown): value is string | null => value === null || typeof value === 'string'

const isCount = (value: unknown): value is number => typeof value === 'number' && Number.isInteger(value) && value >= 1

/** Whether a pending mutation's variables are a change to an order (they are stored untyped). */
export function isOrderEdit(value: unknown): value is OrderEdit {
  if (!isRecord(value) || typeof value.orderId !== 'string' || typeof value.session !== 'string') return false
  if (typeof value.itemId === 'string') {
    const removed = value.removed === undefined || typeof value.removed === 'boolean'
    const quantity = value.quantity === undefined || typeof value.quantity === 'number'
    const count = value.count === undefined || isCount(value.count)
    const alternative = value.alternative === undefined || typeof value.alternative === 'string'
    return removed && quantity && count && alternative
  }
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
      if ('itemId' in edit) {
        const list = shown.shopping_list
        if (list === null) return shown
        const items = list.items.map((item) =>
          item.id === edit.itemId ? changedItem(item, edit) : item,
        )
        return { ...shown, shopping_list: { ...list, items } }
      }
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

/** An item with one change applied, as the server applies it. */
function changedItem(item: ShoppingItem, edit: SetItem): ShoppingItem {
  let changed = { ...item, removed: edit.removed ?? item.removed, quantity: edit.quantity ?? item.quantity }
  const chosen = item.alternatives.find((alternative) => alternative.product_id === edit.alternative)
  if (chosen !== undefined) {
    // Switched: the product it had becomes one of its alternatives.
    const others = item.alternatives.filter((alternative) => alternative !== chosen)
    changed = {
      ...changed,
      product: chosen,
      alternatives: item.product === null ? others : [...others, item.product],
      removed: false,
      // Found, then: the member picked what the store has instead.
      not_found: false,
      skippable_reason: null,
    }
  }
  if (edit.count !== undefined && changed.product !== null) {
    changed = { ...changed, product: { ...changed.product, count: edit.count } }
  }
  return changed
}
