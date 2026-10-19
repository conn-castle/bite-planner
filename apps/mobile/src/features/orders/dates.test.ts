import { afterEach, describe, expect, it, vi } from 'vitest'
import type { GroceryOrder, Quote } from '../../lib/api/types'
import { availableWindows, deliveryAvailable, deliveryExpired, storeDateTime } from './dates'
import {
  coveredDays,
  datesForDelivery,
  shownDeliveryBy,
  defaultDeliveryDate,
  defaultNextDeliveryDate,
  deliveryChoice,
  deliveryWhy,
  followingDates,
  nextDeliveryWhy,
  deliveryBar,
  deliveryGap,
  nextDeliveryBar,
  shownDeliveryDate,
  shownNextDeliveryDate,
} from './dates'

// Time zones are set with vi.stubEnv('TZ', ...), which Node applies at once.
afterEach(() => {
  vi.unstubAllEnvs()
})

describe('live delivery choices', () => {
  const snapshot: Quote = {
    job: { id: 'times', meal_id: null, kind: 'quote', status: 'succeeded', error: null, requested_by: { user_id: 'des', name: 'Des' }, created_at: '2026-10-18T18:00:00Z', started_at: null, finished_at: null, meals: [] },
    current: true, signed_out: false, blocked_because: null, checkout: null,
    availability_current: true, availability_valid_until: '2026-10-18T18:30:00Z',
    availability: { observed_at: '2026-10-18T18:00:00Z', time_zone: 'America/New_York', days: [
      { day: '2026-10-18', windows: [{ start: '13:00:00', end: '15:00:00' }, { start: '15:00:00', end: '17:00:00' }] },
      { day: '2026-10-19', windows: [] },
    ] },
  }
  const order: Pick<GroceryOrder, 'quote' | 'delivery_time_zone'> = { quote: snapshot, delivery_time_zone: 'America/New_York' }

  it('allows remaining same-day windows and keeps the approved arrival deadline strict', () => {
    const now: Date = new Date('2026-10-18T18:05:00Z')
    expect(availableWindows(order, '2026-10-18', now)).toEqual([{ start: '15:00:00', end: '17:00:00' }])
    expect(deliveryAvailable(order, '2026-10-18', '17:00:00', now)).toBe(true)
    expect(deliveryAvailable(order, '2026-10-18', '16:00:00', now)).toBe(false)
    expect(availableWindows(order, '2026-10-19', now)).toEqual([])
    expect(availableWindows(order, '2026-10-20', now)).toBeNull()
  })

  it('requires fresh evidence even to distinguish future planning from offered dates', () => {
    const expired: Date = new Date('2026-10-18T18:30:00Z')
    expect(availableWindows(order, '2026-10-18', expired)).toEqual([])
    expect(availableWindows(order, '2026-10-20', expired)).toEqual([])
  })

  it('uses the store calendar when the phone has already reached tomorrow', () => {
    vi.stubEnv('TZ', 'Pacific/Kiritimati')
    const now: Date = new Date('2026-10-19T02:00:00Z')
    expect(storeDateTime(now, order.delivery_time_zone)).toBe('2026-10-18T22:00:00')
    expect(deliveryExpired('2026-10-19', '09:00:00', order.delivery_time_zone, now)).toBe(false)
    expect(deliveryExpired('2026-10-18', '21:00:00', order.delivery_time_zone, now)).toBe(true)
  })
})

describe('defaultDeliveryDate', () => {
  it('is the target date (the previous order’s next delivery)', () => {
    expect(defaultDeliveryDate({ target_date: '2026-09-28', earliest_delivery_date: '2026-09-25' })).toBe('2026-09-28')
  })

  it('is the target date when that is the earliest possible day', () => {
    expect(defaultDeliveryDate({ target_date: '2026-09-25', earliest_delivery_date: '2026-09-25' })).toBe('2026-09-25')
  })

  it('moves to the earliest delivery date once the target has passed', () => {
    expect(defaultDeliveryDate({ target_date: '2026-09-20', earliest_delivery_date: '2026-09-25' })).toBe('2026-09-25')
  })

  it('has no default for the first order', () => {
    expect(defaultDeliveryDate({ target_date: null, earliest_delivery_date: '2026-09-25' })).toBeNull()
  })
})

describe('shownDeliveryDate', () => {
  const limits = { target_date: '2026-09-28', earliest_delivery_date: '2026-09-25' }

  it('keeps a date the household already chose', () => {
    expect(shownDeliveryDate({ ...limits, delivery_date: '2026-09-30' })).toBe('2026-09-30')
  })

  it('preserves an expired selection until the user chooses again', () => {
    expect(shownDeliveryDate({ ...limits, delivery_date: '2026-09-24' })).toBe('2026-09-24')
  })

  it('shows the default before anything is chosen', () => {
    expect(shownDeliveryDate({ ...limits, delivery_date: null })).toBe('2026-09-28')
    expect(shownDeliveryDate({ target_date: null, earliest_delivery_date: '2026-09-25', delivery_date: null })).toBeNull()
  })
})

describe('defaultNextDeliveryDate', () => {
  it('is the delivery date plus one day per meal', () => {
    expect(defaultNextDeliveryDate('2026-09-28', 3)).toBe('2026-10-01')
    expect(defaultNextDeliveryDate('2026-12-30', 5)).toBe('2027-01-04')
  })

  it('counts calendar days across a daylight-saving change, in any time zone', () => {
    vi.stubEnv('TZ', 'America/New_York') // clocks go back on Nov 1, 2026
    expect(defaultNextDeliveryDate('2026-10-31', 3)).toBe('2026-11-03')
    vi.stubEnv('TZ', 'Australia/Sydney') // clocks go forward on Oct 4, 2026
    expect(defaultNextDeliveryDate('2026-10-03', 2)).toBe('2026-10-05')
  })
})

describe('shownNextDeliveryDate', () => {
  it('keeps a chosen next delivery date that is after the delivery date', () => {
    expect(shownNextDeliveryDate('2026-09-28', '2026-10-03', 3)).toBe('2026-10-03')
  })

  it('uses the default when none is chosen, or the chosen one is not after the delivery date', () => {
    expect(shownNextDeliveryDate('2026-09-28', null, 3)).toBe('2026-10-01')
    expect(shownNextDeliveryDate('2026-09-28', '2026-09-28', 3)).toBe('2026-10-01')
    expect(shownNextDeliveryDate('2026-09-28', '2026-09-26', 3)).toBe('2026-10-01')
  })
})

describe('datesForDelivery', () => {
  it('keeps the next delivery date while it is still after the new delivery date, and the time chosen', () => {
    expect(datesForDelivery('2026-09-29', '15:00:00', '2026-10-02')).toEqual({
      delivery_date: '2026-09-29',
      delivery_by: '15:00:00',
      next_delivery_date: '2026-10-02',
    })
  })

  it('clears the next delivery date when the delivery moves onto or past it', () => {
    expect(datesForDelivery('2026-10-02', null, '2026-10-02').next_delivery_date).toBeNull()
    expect(datesForDelivery('2026-10-05', null, '2026-10-02').next_delivery_date).toBeNull()
  })

  it('leaves an unset next delivery date and time unset', () => {
    expect(datesForDelivery('2026-09-28', null, null)).toEqual({
      delivery_date: '2026-09-28',
      delivery_by: null,
      next_delivery_date: null,
    })
  })
})

describe('shownDeliveryBy', () => {
  it("shows the time chosen, else the household's delivery time", () => {
    expect(shownDeliveryBy({ delivery_by: '15:00:00' }, '18:00:00')).toBe('15:00:00')
    expect(shownDeliveryBy({ delivery_by: null }, '18:00:00')).toBe('18:00:00')
    expect(shownDeliveryBy({ delivery_by: null }, undefined)).toBeNull()
  })
})

describe('coveredDays', () => {
  it('runs from the delivery date up to the day before the next delivery', () => {
    expect(coveredDays('2026-09-28', '2026-10-01')).toEqual(['2026-09-28', '2026-09-29', '2026-09-30'])
  })

  it('is one day when the next delivery is the day after', () => {
    expect(coveredDays('2026-09-28', '2026-09-29')).toEqual(['2026-09-28'])
  })

  it('crosses months and years', () => {
    expect(coveredDays('2026-12-30', '2027-01-02')).toEqual(['2026-12-30', '2026-12-31', '2027-01-01'])
  })
})

describe('deliveryGap', () => {
  it('is zero when the delivery arrives on the target date', () => {
    expect(deliveryGap('2026-09-28', '2026-09-28')).toBe(0)
  })

  it('counts the days with no meals when the delivery comes after the target', () => {
    expect(deliveryGap('2026-09-28', '2026-09-30')).toBe(2)
  })

  it('is negative (days of overlap) when the delivery comes before the target', () => {
    expect(deliveryGap('2026-09-28', '2026-09-27')).toBe(-1)
  })

  it('is null for the first order, which has no target', () => {
    expect(deliveryGap(null, '2026-09-28')).toBeNull()
  })
})

// Today is Fri Sep 25; the earliest delivery is tomorrow.
const TODAY = '2026-09-25'
const EARLIEST = '2026-09-26'
const draft = { number: 2, earliest_delivery_date: EARLIEST, delivery_date: null }
const day = (days: ReturnType<typeof deliveryBar>, date: string) => days.find((item) => item.date === date)

describe('deliveryBar', () => {
  it('keeps today selectable even while a previous order still covers future meals', () => {
    // Order 1 covers through Wed 30.
    const days = deliveryBar({ ...draft, earliest_delivery_date: TODAY, target_date: '2026-10-01' }, TODAY)
    expect(days[0]).toEqual({ date: TODAY, coveredBy: 1, selectable: true, tag: 'today' })
    expect(day(days, '2026-10-01')).toEqual({ date: '2026-10-01', coveredBy: null, selectable: true, tag: 'target' })
  })

  it('starts at today when the previous order’s last covered day is today; today is covered but not selectable', () => {
    const days = deliveryBar({ ...draft, target_date: EARLIEST }, TODAY)
    expect(days[0]).toEqual({ date: TODAY, coveredBy: 1, selectable: false, tag: 'today' })
    expect(days[1]).toEqual({ date: EARLIEST, coveredBy: null, selectable: true, tag: 'target' })
  })

  it('starts at today, greyed out, once the previous order’s meals have run out', () => {
    const days = deliveryBar({ ...draft, target_date: '2026-09-20' }, TODAY)
    expect(days[0]).toEqual({ date: TODAY, coveredBy: null, selectable: false, tag: 'today' })
    // The target has passed, so the earliest delivery is the suggestion.
    expect(days[1]).toEqual({ date: EARLIEST, coveredBy: null, selectable: true, tag: 'soonest' })
    expect(days.some((item) => item.coveredBy !== null)).toBe(false)
  })

  it('starts at today, greyed out, for the first order', () => {
    const days = deliveryBar({ ...draft, number: 1, target_date: null }, TODAY)
    expect(days[0]).toEqual({ date: TODAY, coveredBy: null, selectable: false, tag: 'today' })
    expect(days.slice(1).every((item) => item.selectable && item.coveredBy === null && item.tag === null)).toBe(true)
  })

  it('never offers a day before the earliest delivery date', () => {
    const days = deliveryBar({ ...draft, earliest_delivery_date: '2026-09-28', target_date: null }, TODAY)
    expect(days.filter((item) => !item.selectable).map((item) => item.date)).toEqual([
      TODAY,
      '2026-09-26',
      '2026-09-27',
    ])
  })

  it('runs 30 days past the last day with meals, or today once that has passed, or to a later chosen date', () => {
    expect(deliveryBar({ ...draft, target_date: null }, TODAY).at(-1)?.date).toBe('2026-10-25')
    // The previous order covers through Oct 19.
    expect(deliveryBar({ ...draft, target_date: '2026-10-20' }, TODAY).at(-1)?.date).toBe('2026-11-18')
    expect(deliveryBar({ ...draft, target_date: null, delivery_date: '2026-11-22' }, TODAY).at(-1)?.date).toBe(
      '2026-11-22',
    )
  })

  it('keeps an expired saved date visible before today', () => {
    const days = deliveryBar({ ...draft, target_date: '2026-10-05', delivery_date: '2026-09-24' }, TODAY)
    expect(days[0]).toEqual({ date: '2026-09-24', coveredBy: 1, selectable: false, tag: null })
  })
})

describe('nextDeliveryBar', () => {
  const order = { number: 2 }

  it('starts at the delivery date, which is covered but not selectable', () => {
    const days = nextDeliveryBar(order, '2026-09-28', 3, '2026-10-01')
    expect(days[0]).toEqual({ date: '2026-09-28', coveredBy: 2, selectable: false, tag: 'delivery' })
  })

  it('covers the days up to the chosen next delivery, and marks the recommended day', () => {
    const days = nextDeliveryBar(order, '2026-09-28', 3, '2026-10-03')
    expect(days.filter((item) => item.coveredBy === 2).map((item) => item.date)).toEqual([
      '2026-09-28',
      '2026-09-29',
      '2026-09-30',
      '2026-10-01',
      '2026-10-02',
    ])
    expect(days.filter((item) => item.tag === 'best').map((item) => item.date)).toEqual(['2026-10-01'])
    expect(days.slice(1).every((item) => item.selectable)).toBe(true)
  })

  it('runs a week past the recommended day, or to a later chosen date', () => {
    expect(nextDeliveryBar(order, '2026-09-28', 3, '2026-10-01').at(-1)?.date).toBe('2026-10-08')
    expect(nextDeliveryBar(order, '2026-09-28', 3, '2026-10-20').at(-1)?.date).toBe('2026-10-20')
  })
})

describe('what the dates mean', () => {
  // Dates as said in English, whatever the machine's locale.
  const EN = 'en-US'
  const order = { number: 4, target_date: '2026-09-27', earliest_delivery_date: '2026-09-25' }

  it('names where the delivery date comes from', () => {
    expect(deliveryChoice(order, '2026-09-27')).toBe('target')
    expect(deliveryChoice(order, '2026-09-29')).toBe('pick')
    // The target passed: the soonest delivery takes its place.
    const late = { ...order, target_date: '2026-09-23' }
    expect(deliveryChoice(late, '2026-09-25')).toBe('soonest')
    expect(deliveryChoice({ ...order, target_date: null }, '2026-09-27')).toBe('pick')
  })

  it("says how the delivery date lines up with the previous order's days", () => {
    expect(deliveryWhy(order, '2026-09-27', 3, EN)).toBe("Starts the day after Order 3's last covered day, Sat 26.")
    expect(deliveryWhy(order, '2026-09-29', 3, EN)).toBe('2 days with no meals covered before it arrives (Sun 27 – Mon 28).')
    expect(deliveryWhy(order, '2026-09-26', 3, EN)).toBe('Arrives while Order 3 still covers 1 day. Groceries may overlap.')
  })

  it('says how the meals fit the days until the next delivery', () => {
    expect(nextDeliveryWhy('2026-09-27', '2026-09-30', 3, EN)).toBe('3 meals, one a day, cover Sun 27 – Tue 29.')
    expect(nextDeliveryWhy('2026-09-27', '2026-10-01', 3, EN)).toBe(
      '3 meals cover 4 days, Sun 27 – Wed 30. 1 day needs leftovers or takeout.',
    )
    expect(nextDeliveryWhy('2026-09-27', '2026-09-29', 3, EN)).toBe(
      '3 meals cover 2 days, Sun 27 – Mon 28. 1 meal carries past the next delivery.',
    )
  })

  it("says what the next delivery sets for the following order, by the household's rules", () => {
    expect(followingDates(5, '2026-10-05', 1, 2, '2026-09-24', EN)).toBe(
      'Order 5: decide by Sun, Oct 4, reminders start Fri, Oct 2.',
    )
    expect(followingDates(5, '2026-09-27', 1, 2, '2026-09-24', EN)).toBe(
      'Order 5: decide by Sat, Sep 26, reminders start today.',
    )
  })
})
