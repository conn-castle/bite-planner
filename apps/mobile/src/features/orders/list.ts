import type { GroceryOrder, ShoppingItem, ShoppingList } from '../../lib/api/types'
import { formatQuantity, formatUnit } from '../meals/servings'
import { isRunning } from './batches'

/** A draft's shopping list, as its step shows it. */

/**
 * Where the list is: none started, or one built for older picks with nothing
 * being made (to start); being made; its newest build failed (a build replaced
 * by a newer one is never the newest); or ready for the picks now.
 */
export type ListState = 'start' | 'building' | 'failed' | 'ready'

export function listState(list: ShoppingList | null): ListState {
  if (list === null) return 'start'
  if (list.current) return 'ready'
  if (isRunning(list.job)) return 'building'
  return list.job.status === 'failed' ? 'failed' : 'start'
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

/** "salt, pepper, and olive oil". */
export function spokenList(names: string[]): string {
  if (names.length <= 2) return names.join(' and ')
  return `${names.slice(0, -1).join(', ')}, and ${names.at(-1)}`
}

/** The order's list, if it's ready for its picks now. */
export function readyList(order: Pick<GroceryOrder, 'shopping_list'>): ShoppingList | null {
  return order.shopping_list?.current ? order.shopping_list : null
}
