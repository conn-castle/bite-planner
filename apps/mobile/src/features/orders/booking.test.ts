import { describe, expect, it } from 'vitest'
import type { BookFields, BookState } from '../../lib/api/types'
import { bookingLine, changeable, changeStep, needsAttention } from './booking'

const at = (state: BookState, fields: Partial<BookFields> = {}): BookFields => ({
  book_state: state,
  book_on: '2026-10-05',
  booking: null,
  book_issue: null, can_confirm_absent: false,
  ...fields,
})

describe('what can be done with an approved order', () => {
  it('can be changed or cancelled until something is under way or ordered at the store', () => {
    const states: BookState[] = ['waiting', 'booking', 'booked', 'needs_time', 'needs_product', 'not_booked', 'check_instacart']
    expect(states.filter(changeable)).toEqual(['waiting', 'needs_time', 'needs_product', 'not_booked'])
    expect(states.filter(needsAttention)).toEqual(['needs_time', 'needs_product', 'not_booked', 'check_instacart'])
  })

  it('opens the flow where the choice to make again is: the time, or the list', () => {
    expect(changeStep('needs_time')).toBe('delivery')
    expect(changeStep('needs_product')).toBe('list')
    expect(changeStep('waiting')).toBeUndefined()
  })
})

describe('bookingLine', () => {
  it('says when it books, or that it is booking', () => {
    expect(bookingLine(at('waiting'))).toBe('Books Mon, Oct 5, four days before it arrives')
    expect(bookingLine(at('booking'))).toBe('Booking at Instacart now')
  })

  it('says what Instacart confirmed once booked, and when it was a dry run', () => {
    const booked = { window: { start: '14:00', end: '16:00' }, total_cents: 6529, swaps: [] }
    expect(bookingLine(at('booked', { booking: { ...booked, order_number: '18234567' } }))).toBe(
      '2–4 PM · $65.29 · Instacart order 18234567',
    )
    expect(bookingLine(at('booked', { booking: { ...booked, order_number: 'Dry run' } }))).toBe(
      '2–4 PM · $65.29 · Dry run: nothing was ordered',
    )
    // Settled by hand: nothing known to say.
    expect(
      bookingLine(at('booked', { booking: { order_number: null, window: null, total_cents: null, swaps: [] } })),
    ).toBeNull()
  })

  it('says why it is not booked', () => {
    const issue = { message: 'No delivery window is left on Sat, Oct 3 for the time it needs.', item_ids: [], proposal: null, deferred_at: null }
    expect(bookingLine(at('needs_time', { book_issue: issue }))).toBe(
      'No delivery window is left on Sat, Oct 3 for the time it needs',
    )
  })
})
