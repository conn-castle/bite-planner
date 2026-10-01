import type { Checkout, Quote } from '../../lib/api/types'
import { formatMoney } from '../../lib/products'
import { isRunning } from './batches'

/** The final review's total: a quote of the order at the store, as the review shows it. */

/**
 * Where the total is: none for the order as it is now (to start: the first, or
 * the products, counts, or tip changed since), being read, its reading failed,
 * read but Instacart won't check the cart out (below the store's minimum), or read.
 */
export type QuoteState = 'start' | 'reading' | 'failed' | 'blocked' | 'ready'

export function quoteState(quote: Quote | null): QuoteState {
  if (quote === null || !quote.current) return 'start'
  if (isRunning(quote.job)) return 'reading'
  if (quote.job.status === 'failed') return 'failed'
  return quote.blocked_because !== null ? 'blocked' : 'ready'
}

/**
 * What Instacart does when an item is out of stock, from its setting on each
 * item ("Replace with best match"): "Best match", or "Varies by item".
 */
export function replacementShown(settings: readonly string[]): string {
  if (settings.length !== 1) return 'Varies by item'
  const shown = settings[0].replace(/^replace with /i, '')
  return shown.charAt(0).toUpperCase() + shown.slice(1)
}

/** "Items $113.79 · Delivery free · Service fee $6.74 · Tip $11.38": what the total is made of, on one line. */
export function totalParts(checkout: Checkout): string {
  const fees = checkout.fees.map((fee) => {
    // Instacart labels delivery by how it's priced ("Delivery w/ scheduled & Instacart+").
    const label = /^delivery\b/i.test(fee.label) ? 'Delivery' : fee.label
    return `${label} ${fee.cents === 0 ? 'free' : formatMoney(fee.cents)}`
  })
  return [`Items ${formatMoney(checkout.items_cents)}`, ...fees, `Tip ${formatMoney(checkout.tip_cents)}`].join(' · ')
}
