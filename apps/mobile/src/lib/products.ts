import type { Product, ShoppingItem } from './api/types'

/** A store product, as the shopping list shows it. */

/** "$2.29", "$1,234.00". */
export function formatMoney(cents: number): string {
  return `$${(cents / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

/** Sold by the pound, bought by the package: its price is an estimate. */
export function isWeighed(product: Product): boolean {
  return product.price_per_lb_cents !== null
}

/** "2 lb", or for weighed items "$3.79/lb · about 4.2 lb". */
export function productSize(product: Product): string {
  if (product.price_per_lb_cents !== null && product.estimated_lb !== null) {
    return `${formatMoney(product.price_per_lb_cents)}/lb · about ${Number(product.estimated_lb.toFixed(2))} lb`
  }
  return product.size ?? ''
}

/** Still to decide: the store doesn't have it, and the member hasn't picked an alternative or skipped it. */
export function isUndecided(item: ShoppingItem): boolean {
  return item.not_found && item.product === null && !item.removed
}

/** What its count costs ("$4.58"), or null if the store shows no price. */
export function productCost(product: Product): string | null {
  return product.price_cents === null ? null : formatMoney(product.price_cents * product.count)
}
