import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  coveredDays,
  datesForDelivery,
  defaultDeliveryDate,
  defaultNextDeliveryDate,
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

  it('falls back to the default when the chosen date is no longer possible', () => {
    expect(shownDeliveryDate({ ...limits, delivery_date: '2026-09-24' })).toBe('2026-09-28')
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
  it('keeps the next delivery date while it is still after the new delivery date', () => {
    expect(datesForDelivery('2026-09-29', '2026-10-02')).toEqual({
      delivery_date: '2026-09-29',
      next_delivery_date: '2026-10-02',
    })
  })

  it('clears the next delivery date when the delivery moves onto or past it', () => {
    expect(datesForDelivery('2026-10-02', '2026-10-02').next_delivery_date).toBeNull()
    expect(datesForDelivery('2026-10-05', '2026-10-02').next_delivery_date).toBeNull()
  })

  it('leaves an unset next delivery date unset', () => {
    expect(datesForDelivery('2026-09-28', null)).toEqual({ delivery_date: '2026-09-28', next_delivery_date: null })
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
  it('starts at the last day the previous order covers, not earlier', () => {
    // Order 1 covers through Wed 30.
    const days = deliveryBar({ ...draft, target_date: '2026-10-01' }, TODAY)
    expect(days[0]).toEqual({ date: '2026-09-30', coveredBy: 1, selectable: true, tag: null })
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

  it('runs two weeks past the earliest delivery date, or to a later target or chosen date', () => {
    expect(deliveryBar({ ...draft, target_date: null }, TODAY).at(-1)?.date).toBe('2026-10-09')
    expect(deliveryBar({ ...draft, target_date: '2026-10-20' }, TODAY).at(-1)?.date).toBe('2026-10-20')
    expect(deliveryBar({ ...draft, target_date: null, delivery_date: '2026-10-22' }, TODAY).at(-1)?.date).toBe(
      '2026-10-22',
    )
  })

  it('reaches back to a chosen date earlier than where it would start', () => {
    const days = deliveryBar({ ...draft, target_date: '2026-10-05', delivery_date: '2026-10-01' }, TODAY)
    expect(days[0]).toEqual({ date: '2026-10-01', coveredBy: 1, selectable: true, tag: null })
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
