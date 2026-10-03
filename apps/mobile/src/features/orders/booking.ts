import type { BookFields, BookState } from '../../lib/api/types'
import { formatDate, formatWindow } from '../../lib/dates'
import { formatMoney } from '../../lib/products'
import type { IconName } from '../../ui/Icon'

/**
 * Where an approved order is with the store (backend booking.py): it waits for
 * the day it books (four days before delivery), books at Instacart, and is
 * booked; or it isn't, and says why and what to do.
 */

/** The order number development's dry run shows (backend booking.DRY_RUN). */
export const DRY_RUN = 'Dry run'

/** What its card and page call where it is: short, for a chip (reviewed 2026-10-02). */
export const BOOK_STATE_LABEL: Record<BookState, string> = {
  waiting: 'Waiting to book',
  booking: 'Booking',
  booked: 'Order placed',
  needs_time: 'Pick a time',
  needs_product: 'Pick a product',
  not_booked: 'Not booked',
  check_instacart: 'Check Instacart',
}

/** Its chip's icon. */
export const BOOK_STATE_ICON: Record<BookState, IconName> = {
  waiting: 'hourglass',
  booking: 'repeat',
  booked: 'bag',
  needs_time: 'clock',
  needs_product: 'search',
  not_booked: 'alert',
  check_instacart: 'info',
}

/** It needs someone: a time or a product to choose, a failure, or a click to settle. */
export function needsAttention(state: BookState): boolean {
  return state === 'needs_time' || state === 'needs_product' || state === 'not_booked' || state === 'check_instacart'
}

/** Nothing is under way at the store and nothing was clicked: it can still be changed or cancelled. */
export function changeable(state: BookState): boolean {
  return state === 'waiting' || state === 'needs_time' || state === 'needs_product' || state === 'not_booked'
}

/** Where the flow opens when it's changed: the step that needs choosing again, else where it got to. */
export function changeStep(state: BookState): 'delivery' | 'list' | undefined {
  return state === 'needs_time' ? 'delivery' : state === 'needs_product' ? 'list' : undefined
}

/**
 * One line on where it is: "Books Mon, Oct 5, four days before it arrives",
 * "Booking at Instacart now", "2–4 PM · $65.29 · Instacart order 18234567", or
 * why it isn't booked.
 */
export function bookingLine(order: BookFields): string | null {
  switch (order.book_state) {
    case null:
      return null
    case 'waiting':
      return order.book_on ? `Books ${formatDate(order.book_on, 'medium')}, four days before it arrives` : null
    case 'booking':
      return 'Booking at Instacart now'
    case 'booked': {
      const booked = order.booking
      if (!booked) return null
      const parts = [
        booked.window ? formatWindow(booked.window) : null,
        booked.total_cents !== null ? formatMoney(booked.total_cents) : null,
        booked.order_number === DRY_RUN
          ? 'Dry run: nothing was ordered'
          : booked.order_number
            ? `Instacart order ${booked.order_number}`
            : null,
      ]
      return parts.filter(Boolean).join(' · ') || null
    }
    default:
      // Said as a line of its own: without its last period.
      return order.book_issue?.message.replace(/\.$/, '') ?? null
  }
}
