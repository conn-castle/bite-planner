import { describe, expect, it } from 'vitest'
import { dayLabel, groupByDay } from './days'

const now = new Date(2026, 8, 24, 9, 30) // Thursday, Sep 24 2026

describe('dayLabel', () => {
  it('names recent days the way people say them', () => {
    expect(dayLabel(new Date(2026, 8, 24, 0, 5), now, 'en-US')).toBe('Today')
    expect(dayLabel(new Date(2026, 8, 23, 23, 55), now, 'en-US')).toBe('Yesterday')
    expect(dayLabel(new Date(2026, 8, 21, 12), now, 'en-US')).toBe('Monday')
  })

  it('uses the date from a week back', () => {
    expect(dayLabel(new Date(2026, 8, 17, 12), now, 'en-US')).toBe('September 17')
    expect(dayLabel(new Date(2025, 11, 30, 12), now, 'en-US')).toBe('December 30, 2025')
  })
})

describe('groupByDay', () => {
  it('keeps order and starts a section at each new day', () => {
    const items = [
      new Date(2026, 8, 24, 9),
      new Date(2026, 8, 24, 7),
      new Date(2026, 8, 23, 20),
      new Date(2026, 8, 10, 12),
    ]
    const sections = groupByDay(items, (date) => date, now, 'en-US')
    expect(sections.map((section) => [section.label, section.items.length])).toEqual([
      ['Today', 2],
      ['Yesterday', 1],
      ['September 10', 1],
    ])
    expect(sections[0].items).toEqual(items.slice(0, 2))
  })

  it('returns no sections for no items', () => {
    expect(groupByDay([], (date: Date) => date, now)).toEqual([])
  })
})
