import type { GroceryOrder, ShoppingItem, ShoppingList } from '../../lib/api/types'
import { formatMoney } from '../../lib/products'
import { formatQuantity, formatUnit } from '../meals/servings'
import { isRunning } from './batches'

/** A draft's shopping list, as its step shows it. */

/**
 * Where the list is: none started, or one built for older picks with nothing
 * being made (to start); being made; its newest build failed (a build replaced
 * by a newer one is never the newest); built for the picks now but not shopped
 * (to shop: e.g. built before shopping started with each list); being shopped
 * at the store; its shopping failed; or shopped, its products ready.
 */
export type ListState = 'start' | 'building' | 'failed' | 'shopStart' | 'shopping' | 'shopFailed' | 'ready'

export function listState(list: ShoppingList | null): ListState {
  if (list === null) return 'start'
  if (list.current) {
    if (list.shop === null) return 'shopStart'
    if (isRunning(list.shop)) return 'shopping'
    return list.shop.status === 'succeeded' ? 'ready' : 'shopFailed'
  }
  if (isRunning(list.job)) return 'building'
  return list.job.status === 'failed' ? 'failed' : 'start'
}

/** The items the store doesn't have that the member hasn't decided on: picked an alternative or skipped. */
export function undecided(items: ShoppingItem[]): ShoppingItem[] {
  return items.filter((item) => !item.on_hand && item.not_found && item.product === null && !item.removed)
}

/** "2 lb", "8", "1½ cups": how much of an item; nothing when the recipes give no amount. */
export function itemAmount({ quantity, unit }: Pick<ShoppingItem, 'quantity' | 'unit'>): string {
  if (quantity === null) return ''
  return unit === null ? formatQuantity(quantity) : `${formatQuantity(quantity)} ${formatUnit(unit, quantity)}`
}

/** How much − and + change an item: one of a counted item, finer steps for small amounts. */
export function quantityStep({ quantity, unit }: Pick<ShoppingItem, 'quantity' | 'unit'>): number {
  if (unit === null || quantity === null) return 1
  return quantity < 2 ? 0.25 : quantity < 10 ? 0.5 : 1
}

/** "for Fajitas, Curry": the meals an item is for, by their names. */
export function forMeals(item: Pick<ShoppingItem, 'meal_ids'>, names: ReadonlyMap<string, string>): string {
  const meals = item.meal_ids.flatMap((id) => {
    const name = names.get(id)
    return name === undefined ? [] : [name]
  })
  return meals.length > 0 ? `for ${meals.join(', ')}` : ''
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
