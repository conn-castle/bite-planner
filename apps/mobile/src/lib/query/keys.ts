import type { ISODate } from '../api/types'

/**
 * Every server query's key, in one place: features write into each other's
 * caches (e.g. a rating shows on the meal page, in History, and in a draft).
 */
export const queryKeys = {
  nextOrder: ['next-order'],
  /** Every placed-orders list: Home's recent window and History's full list. */
  placedOrders: ['placed-orders'],
  placedOrdersFrom: (coveringFrom?: ISODate) => ['placed-orders', coveringFrom ?? 'all'],
  /** Every single order (drafts, as the create-order flow reads them). */
  orders: ['grocery-order'],
  order: (orderId: string) => ['grocery-order', orderId],
  meal: (mealId: string) => ['meal', mealId],
  /** The household's settings and members. */
  household: ['household'],
} as const
