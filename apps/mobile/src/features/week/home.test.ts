import { afterEach, describe, expect, it, vi } from 'vitest'
import type { GroceryOrder, Job, NextOrder, PlacedOrder } from '../../lib/api/types'
import { dayCaption, followingOrder, homeCards, homeStrip, phase, urgency } from './home'

const TODAY = '2026-09-24'

// Time zones are set with vi.stubEnv('TZ', ...), which Node applies at once.
afterEach(() => {
  vi.unstubAllEnvs()
})

function placed(number: number, delivery: string, next: string, placedAt = '2026-09-10T15:00:00Z'): PlacedOrder {
  return {
    id: `order-${number}`,
    number,
    placed_at: placedAt,
    placed_by: { user_id: 'sam', name: 'Sam' },
    delivery_date: delivery,
    next_delivery_date: next,
    meals: [],
  }
}

function nextOrder(number: number, target: string | null, decideBy: string | null, draft: GroceryOrder | null = null): NextOrder {
  return { number, target_date: target, decide_by_date: decideBy, earliest_delivery_date: '2026-09-25', draft }
}

function draft(number: number, jobs: Job[]): GroceryOrder {
  return {
    id: 'draft',
    number,
    status: 'draft',
    created_by: { user_id: 'sam', name: 'Sam' },
    created_at: '2026-09-23T12:00:00Z',
    target_date: '2026-09-28',
    decide_by_date: '2026-09-27',
    earliest_delivery_date: '2026-09-25',
    delivery_date: null,
    next_delivery_date: null,
    jobs,
    meal_ids: [],
  }
}

function job(status: Job['status'], meals: number): Job {
  return {
    id: `job-${status}`,
    kind: 'generate_meals',
    status,
    error: null,
    requested_by: { user_id: 'sam', name: 'Sam' },
    created_at: '2026-09-23T12:00:00Z',
    meals: Array.from({ length: meals }, (_, index) => ({
      id: `meal-${index}`,
      name: `Meal ${index}`,
      description: null,
      active_time_minutes: null,
      total_time_minutes: null,
      rating: null,
    })),
  }
}

describe('phase', () => {
  const order = { delivery_date: '2026-09-26', next_delivery_date: '2026-09-29' }

  it('follows an order from on the way to finished', () => {
    expect(phase(order, '2026-09-25')).toBe('waiting')
    expect(phase(order, '2026-09-26')).toBe('arriving')
    expect(phase(order, '2026-09-28')).toBe('onhand')
    expect(phase(order, '2026-09-29')).toBe('done')
  })
})

describe('urgency', () => {
  it('is due today or tomorrow, late once the decide-by date passes, and calm before', () => {
    expect(urgency({ decide_by_date: null }, TODAY)).toBe('first')
    expect(urgency({ decide_by_date: '2026-09-23' }, TODAY)).toBe('late')
    expect(urgency({ decide_by_date: TODAY }, TODAY)).toBe('soon')
    expect(urgency({ decide_by_date: '2026-09-25' }, TODAY)).toBe('soon')
    expect(urgency({ decide_by_date: '2026-09-26' }, TODAY)).toBe('calm')
  })
})

describe('homeCards', () => {
  const kinds = (cards: ReturnType<typeof homeCards>) =>
    cards.map((card) => (card.kind === 'placed' ? `order ${card.order.number}` : 'next') + (card.top ? ' (top)' : ''))

  it('puts the next order first when it is due, with the meals on hand under it', () => {
    const onHand = placed(3, '2026-09-22', '2026-09-26')
    expect(kinds(homeCards([onHand], nextOrder(4, '2026-09-26', '2026-09-25'), TODAY))).toEqual([
      'next (top)',
      'order 3',
    ])
  })

  it('puts groceries on the way before an order not due yet', () => {
    const onHand = placed(3, '2026-09-22', '2026-09-26')
    const waiting = placed(4, '2026-09-26', '2026-10-01')
    expect(kinds(homeCards([onHand, waiting], nextOrder(5, '2026-10-01', '2026-09-30'), TODAY))).toEqual([
      'order 4 (top)',
      'order 3',
      'next',
    ])
  })

  it('never makes meals on hand the next step', () => {
    const onHand = placed(3, '2026-09-22', '2026-09-30')
    expect(kinds(homeCards([onHand], nextOrder(4, '2026-09-30', '2026-09-29'), TODAY))).toEqual([
      'next (top)',
      'order 3',
    ])
  })

  it('puts a draft with ideas, or ideas on the way, above everything', () => {
    const arriving = placed(3, TODAY, '2026-09-28')
    for (const batch of [job('running', 0), job('succeeded', 20)]) {
      const next = nextOrder(4, '2026-09-28', '2026-09-27', draft(4, [batch]))
      expect(kinds(homeCards([arriving], next, TODAY))).toEqual(['next (top)', 'order 3'])
    }
  })

  it('leaves out orders whose meals are all behind', () => {
    const finished = placed(2, '2026-09-18', '2026-09-22')
    expect(kinds(homeCards([finished], nextOrder(3, '2026-09-22', '2026-09-21'), TODAY))).toEqual(['next (top)'])
  })
})

describe('homeStrip', () => {
  it('runs from ten days back to 90 ahead, earlier for an order still covering the days', () => {
    const plain = homeStrip([], nextOrder(1, null, null), TODAY)
    expect([plain[0].date, plain.at(-1)?.date]).toEqual(['2026-09-14', '2026-12-23'])

    const long = homeStrip([placed(1, '2026-09-10', '2026-09-26')], nextOrder(2, '2026-09-26', '2026-09-25'), TODAY)
    expect(long[0].date).toBe('2026-09-10')
  })

  it('marks covered days, deliveries, the decide-by date, and the day an order was placed', () => {
    // The day an order was placed is the phone's calendar day.
    vi.stubEnv('TZ', 'America/New_York')
    const onHand = placed(3, '2026-09-22', '2026-09-26', '2026-09-20T15:00:00Z')
    const waiting = placed(4, '2026-09-26', '2026-10-01')
    const days = homeStrip([onHand, waiting], nextOrder(5, '2026-10-01', '2026-09-30'), TODAY)
    const day = (date: string) => days.find((each) => each.date === date)

    expect(day('2026-09-23')).toMatchObject({ coveredBy: 3, coverAhead: false, past: true, tag: null })
    expect(day(TODAY)).toMatchObject({ coveredBy: 3, past: false, tag: 'today' })
    expect(day('2026-09-26')).toMatchObject({
      coveredBy: 4,
      coverAhead: true,
      marks: [{ kind: 'delivery', order: 4, placed: true }],
    })
    expect(day('2026-09-30')?.marks).toEqual([{ kind: 'decide', late: false }])
    expect(day('2026-10-01')).toMatchObject({ coveredBy: null, marks: [{ kind: 'delivery', order: 5, placed: false }] })
    // Placed at 11 AM on Sep 20 in New York.
    expect(day('2026-09-20')?.marks).toEqual([{ kind: 'decided' }])
  })

  it('marks a passed decide-by date as late', () => {
    const days = homeStrip([], nextOrder(2, '2026-09-23', '2026-09-22'), TODAY)
    expect(days.find((each) => each.date === '2026-09-22')?.marks).toEqual([{ kind: 'decide', late: true }])
  })
})

describe('dayCaption', () => {
  const onHand = placed(3, '2026-09-22', '2026-09-26')
  const next = nextOrder(4, '2026-09-26', '2026-09-25')

  it('says which day of an order a covered day is', () => {
    expect(dayCaption([onHand], next, TODAY)).toEqual({ order: 3, lines: ['Order 3 meals · day 3 of 4'] })
  })

  it('names deliveries and the day to decide by', () => {
    expect(dayCaption([onHand], next, '2026-09-25')).toEqual({
      order: 3,
      lines: ['Order 3 meals · day 4 of 4', 'Decide Order 4'],
    })
    expect(dayCaption([onHand], next, '2026-09-26')).toEqual({
      order: 4,
      lines: ['Order 4 target delivery (not placed)'],
    })
  })

  it('says when no meals cover a day, or nothing is planned', () => {
    expect(dayCaption([onHand], next, '2026-10-10').lines).toEqual(['No meals covered'])
    expect(dayCaption([], nextOrder(1, null, null), TODAY).lines).toEqual(['Nothing planned yet'])
  })
})

describe('followingOrder', () => {
  const third = placed(3, '2026-09-22', '2026-09-26')
  const fourth = placed(4, '2026-09-26', '2026-10-01')
  const next = nextOrder(5, '2026-10-01', '2026-09-30')

  it('is the placed order after it, or the next order, or none for an older order', () => {
    expect(followingOrder(third, [third, fourth], next)).toEqual({ kind: 'placed', order: fourth })
    expect(followingOrder(fourth, [third, fourth], next)).toEqual({ kind: 'next', next })
    // Order 1 is followed by Order 2, which is no longer on Home.
    expect(followingOrder(placed(1, '2026-09-14', '2026-09-18'), [third, fourth], next)).toBeNull()
  })
})
