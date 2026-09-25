import { describe, expect, it } from 'vitest'
import { daysOfWeek, formatWeekRange, isSameDay } from './week'

describe('daysOfWeek', () => {
  it('runs Sunday to Saturday around the given day', () => {
    const days = daysOfWeek(new Date(2026, 8, 24, 18, 30)) // Thursday, Sep 24
    expect(days.map((day) => [day.getMonth(), day.getDate(), day.getDay()])).toEqual([
      [8, 20, 0],
      [8, 21, 1],
      [8, 22, 2],
      [8, 23, 3],
      [8, 24, 4],
      [8, 25, 5],
      [8, 26, 6],
    ])
  })

  it('starts on the day itself when it is a Sunday', () => {
    expect(daysOfWeek(new Date(2026, 8, 20))[0].getDate()).toBe(20)
  })

  it('crosses month and year boundaries', () => {
    const days = daysOfWeek(new Date(2026, 11, 31)) // Thursday, Dec 31
    expect(days[0]).toEqual(new Date(2026, 11, 27))
    expect(days[6]).toEqual(new Date(2027, 0, 2))
  })
})

describe('isSameDay', () => {
  it('ignores the time of day', () => {
    expect(isSameDay(new Date(2026, 8, 24, 0, 1), new Date(2026, 8, 24, 23, 59))).toBe(true)
    expect(isSameDay(new Date(2026, 8, 24), new Date(2026, 8, 25))).toBe(false)
  })
})

describe('formatWeekRange', () => {
  it('names the month once within a month', () => {
    expect(formatWeekRange(daysOfWeek(new Date(2026, 8, 24)), 'en-US')).toBe('Sep 20 – 26')
  })

  it('names both months across months', () => {
    expect(formatWeekRange(daysOfWeek(new Date(2026, 8, 30)), 'en-US')).toBe('Sep 27 – Oct 3')
  })
})
