import { describe, expect, it } from 'vitest'
import type { Job, Quote } from '../../lib/api/types'
import { quoteState, replacementShown, totalParts } from './quote'

const job = (status: Job['status']): Job => ({
  id: 'quote-1',
  kind: 'quote',
  status,
  error: null,
  requested_by: { user_id: 'mom', name: 'Mom' },
  created_at: '2026-09-28T20:00:00Z',
  meals: [],
})

const quote = (fields: Partial<Quote>): Quote => ({
  job: job('succeeded'),
  current: true,
  signed_out: false,
  blocked_because: null,
  checkout: null,
  ...fields,
})

describe('quoteState', () => {
  it('starts one when there is none for the order as it is now', () => {
    expect(quoteState(null)).toBe('start')
    // Read for other products, counts, or tip.
    expect(quoteState(quote({ current: false }))).toBe('start')
    expect(quoteState(quote({ current: false, job: job('running') }))).toBe('start')
  })

  it('waits while it is read, and says when reading failed', () => {
    expect(quoteState(quote({ job: job('pending') }))).toBe('reading')
    expect(quoteState(quote({ job: job('running') }))).toBe('reading')
    expect(quoteState(quote({ job: job('failed') }))).toBe('failed')
  })

  it("tells a total from a cart the store won't take", () => {
    expect(quoteState(quote({}))).toBe('ready')
    expect(quoteState(quote({ blocked_because: '$10 Min. to checkout' }))).toBe('blocked')
  })
})

describe('replacementShown', () => {
  it("shows the one setting all the items have, or that they differ", () => {
    expect(replacementShown(['Replace with best match'])).toBe('Best match')
    expect(replacementShown(["Don't replace"])).toBe("Don't replace")
    expect(replacementShown(['Replace with best match', "Don't replace"])).toBe('Varies by item')
  })
})

describe('totalParts', () => {
  it("lists what the total is made of, with delivery named plainly and free when it costs nothing", () => {
    expect(
      totalParts({
        items_cents: 11379,
        fees: [
          { label: 'Delivery w/ scheduled & Instacart+', cents: 0 },
          { label: 'Service fee', cents: 674 },
        ],
        tip_cents: 1138,
        total_cents: 13191,
        address: '1 Main St Rochester, NY 14604',
        card: 'Visa ending 4242',
        replacements: [],
      }),
    ).toBe('Items $113.79 · Delivery free · Service fee $6.74 · Tip $11.38')
  })
})
