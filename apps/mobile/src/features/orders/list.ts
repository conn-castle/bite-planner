import type { GroceryOrder, ShoppingItem, ShoppingList } from '../../lib/api/types'
import { formatMoney, isUndecided } from '../../lib/products'
import { formatQuantity, formatUnit } from '../meals/servings'
import { isRunning } from './batches'

/** A draft's shopping list, as its step shows it. */

/**
 * Where the list is: no build for the accepted inputs (to start); a matching
 * build being made or genuinely failed; built for the picks now but not shopped
 * (to shop: e.g. built before shopping started with each list); being shopped
 * at the store; its shopping failed; or shopped, its products ready.
 */
export type ListState = 'start' | 'building' | 'failed' | 'shopStart' | 'shopping' | 'shopFailed' | 'ready'

export function listState(list: ShoppingList | null): ListState {
  if (list === null || !list.source_current) return 'start'
  if (list.current) {
    if (list.address_problem !== null) return 'shopFailed'
    if (!list.shop_current) return 'shopStart'
    if (list.shop === null) return 'shopStart'
    if (isRunning(list.shop)) return 'shopping'
    return list.shop.status === 'succeeded' ? 'ready' : 'shopFailed'
  }
  if (isRunning(list.job)) return 'building'
  return list.job.status === 'failed' ? 'failed' : 'start'
}

/** The store the order's list is shopped at: "Wegmans", or "the store" before it has a list. */
export function storeName(order: Pick<GroceryOrder, 'shopping_list'>): string {
  return order.shopping_list?.store ?? 'the store'
}

/** The items to buy the store doesn't have that the member hasn't decided on: picked an alternative or skipped. */
export function undecided(items: ShoppingItem[]): ShoppingItem[] {
  return items.filter((item) => !item.on_hand && isUndecided(item))
}

/** "2 lb", "8", "1½ cups": how much of an item; nothing when the recipes give no amount. */
export function itemAmount({ quantity, unit }: Pick<ShoppingItem, 'quantity' | 'unit'>): string {
  if (quantity === null) return ''
  return unit === null ? formatQuantity(quantity) : `${formatQuantity(quantity)} ${formatUnit(unit, quantity)}`
}

/** "Fajitas, Curry": the meals an item is for, by their names. */
export function mealNames(item: Pick<ShoppingItem, 'meal_ids'>, names: ReadonlyMap<string, string>): string {
  return item.meal_ids
    .flatMap((id) => {
      const name = names.get(id)
      return name === undefined ? [] : [name]
    })
    .join(', ')
}

/** The items to buy, and those assumed on hand (shown only on request). */
export function splitList(items: ShoppingItem[]): { toBuy: ShoppingItem[]; onHand: ShoppingItem[] } {
  return { toBuy: items.filter((item) => !item.on_hand), onHand: items.filter((item) => item.on_hand) }
}

/** How many items are ordered, and how many were taken off. */
export function listCounts(items: ShoppingItem[]): { ordered: number; removed: number } {
  const toBuy = items.filter((item) => !item.on_hand)
  return { ordered: toBuy.filter((item) => !item.removed).length, removed: toBuy.filter((item) => item.removed).length }
}

/**
 * The products bought (not skipped), what those with a price cost before fees
 * (in cents), and how many have no price (so the total is only part of it).
 */
export function productTotals(items: ShoppingItem[]): { count: number; cents: number; unpriced: number } {
  const bought = items.flatMap((item) => (item.on_hand || item.removed || item.product === null ? [] : [item.product]))
  return {
    count: bought.length,
    cents: bought.reduce((sum, p) => sum + (p.price_cents ?? 0) * p.count, 0),
    unpriced: bought.filter((p) => p.price_cents === null).length,
  }
}

/** "$109.50", or "$109.50+" when some products have no price. */
export function totalShown({ cents, unpriced }: { cents: number; unpriced: number }): string {
  return `${formatMoney(cents)}${unpriced > 0 ? '+' : ''}`
}

/**
 * The order's list, if it's ready for its picks now: made, shopped, every item
 * the store doesn't have decided, and something left to buy.
 */
export function readyList(order: Pick<GroceryOrder, 'shopping_list'>): ShoppingList | null {
  const list = order.shopping_list
  if (list === null || listState(list) !== 'ready' || undecided(list.items).length > 0) return null
  if (productTotals(splitList(list.items).toBuy).count === 0) return null
  return list
}

/** Existing work affected by a replacement, including preserved items from an older build. */
export function hasShoppingWork(order: Pick<GroceryOrder, 'shopping_list' | 'jobs'>, action: 'build' | 'shop' | 'recipe'): boolean {
  const list = order.shopping_list
  if (list === null) return false
  if (action === 'shop') {
    return (list.shop !== null && list.shop.status !== 'failed') || list.items.some((item) => item.product !== null)
  }
  return list.items.length > 0 || order.jobs.some((job) =>
    (job.kind === 'build_list' || job.kind === 'shop') && job.status !== 'failed')
}

/** Matching active and finished builds are reused when leaving Servings. */
export function replacesList(order: Pick<GroceryOrder, 'shopping_list' | 'jobs'>): boolean {
  const list = order.shopping_list
  const reusable: boolean = list !== null && list.source_current && list.job.status !== 'failed'
  return !reusable && hasShoppingWork(order, 'build')
}
