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

/**
 * Each change's variables say which change they are (`kind`): they are stored
 * untyped, with the other requests made in the same scope (such as placing the
 * order), and only a change is laid over the order.
 */

/** A pick, with the servings it starts at (the household's default) shown until it's saved; or an unpick. */
export type SetMeal = InSession & { kind: 'meal'; orderId: string; mealId: string } & (
    | { picked: true; servings: number }
    | { picked: false }
  )

/** How many to make of one of the picks. */
export interface SetServings extends InSession {
  kind: 'servings'
  orderId: string
  mealId: string
  servings: number
}

export interface SetDates extends InSession {
  kind: 'dates'
  orderId: string
  dates: OrderDates
}

/**
 * The ✕ on a suggestion: "don't show meals like this" (the household's
 * `not_for_us` rating), which also takes it out of the picks; or undoing it.
 */
export interface SetNotForUs extends InSession {
  kind: 'not-for-us'
  orderId: string
  mealId: string
  notForUs: boolean
}

/**
 * A shopping list item taken off the list (or put back; for a product, not
 * bought), how many of its product, or switched to one of its alternatives (its
 * product id).
 */
export interface SetItem extends InSession {
  kind: 'item'
  orderId: string
  itemId: string
  removed?: boolean
  count?: number
  alternative?: string
}

export type OrderEdit = SetMeal | SetServings | SetDates | SetNotForUs | SetItem

const isDate = (value: unknown): value is string | null => value === null || typeof value === 'string'

const isCount = (value: unknown): value is number => typeof value === 'number' && Number.isInteger(value) && value >= 1

/** Whether a pending mutation's variables are a change to an order (they are stored untyped). */
export function isOrderEdit(value: unknown): value is OrderEdit {
  if (!isRecord(value) || typeof value.orderId !== 'string' || typeof value.session !== 'string') return false
  switch (value.kind) {
    case 'item': {
      const removed = value.removed === undefined || typeof value.removed === 'boolean'
      const count = value.count === undefined || isCount(value.count)
      const alternative = value.alternative === undefined || typeof value.alternative === 'string'
      return typeof value.itemId === 'string' && removed && count && alternative
    }
    case 'meal':
      return typeof value.mealId === 'string' && (value.picked === false || (value.picked === true && isCount(value.servings)))
    case 'servings':
      return typeof value.mealId === 'string' && isCount(value.servings)
    case 'not-for-us':
      return typeof value.mealId === 'string' && typeof value.notForUs === 'boolean'
    case 'dates': {
      const { dates } = value
      return (
        isRecord(dates) && isDate(dates.delivery_date) && isDate(dates.delivery_by) && isDate(dates.next_delivery_date)
      )
    }
    default:
      return false
  }
}

/** The order as it will be once every change is saved. */
export function applyEdits(order: GroceryOrder, edits: readonly OrderEdit[]): GroceryOrder {
  return edits
    .filter((edit) => edit.orderId === order.id)
    .reduce((shown, edit) => {
      switch (edit.kind) {
        case 'dates':
          return { ...shown, ...edit.dates }
        case 'item': {
          const list = shown.shopping_list
          if (list === null) return shown
          const items = list.items.map((item) => (item.id === edit.itemId ? changedItem(item, edit) : item))
          return { ...shown, shopping_list: { ...list, items } }
        }
        case 'meal': {
          const others = shown.picks.filter((pick) => pick.meal_id !== edit.mealId)
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
        case 'not-for-us': {
          const rating = edit.notForUs ? 'not_for_us' : null
          return {
            ...shown,
            jobs: shown.jobs.map((job) => ({
              ...job,
              meals: job.meals.map((meal) => (meal.id === edit.mealId ? { ...meal, rating } : meal)),
            })),
            picks: edit.notForUs ? shown.picks.filter((pick) => pick.meal_id !== edit.mealId) : shown.picks,
          }
        }
        case 'servings':
          // Only a pick has servings (the server refuses the rest).
          return {
            ...shown,
            picks: shown.picks.map((pick) => (pick.meal_id === edit.mealId ? { ...pick, servings: edit.servings } : pick)),
          }
        default:
          return unknownEdit(edit)
      }
    }, order)
}

function unknownEdit(edit: never): never {
  throw new Error(`Unknown order edit: ${JSON.stringify(edit)}`)
}

/** An item with one change applied, as the server applies it. */
function changedItem(item: ShoppingItem, edit: SetItem): ShoppingItem {
  let changed = { ...item, removed: edit.removed ?? item.removed }
  const curated = item.alternatives.some((alternative) => alternative.product_id === edit.alternative)
  const source = curated ? item.alternatives : item.more_alternatives
  const chosen = source.find((alternative) => alternative.product_id === edit.alternative)
  if (chosen !== undefined) {
    // Switched: the product it had becomes one of its alternatives.
    const others = source.filter((alternative) =>
      alternative.product_id !== chosen.product_id && alternative.product_id !== item.product?.product_id,
    )
    const alternatives = item.product === null ? others : [...others, item.product]
    changed = {
      ...changed,
      product: chosen,
      ...(curated ? { alternatives } : { more_alternatives: alternatives }),
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
