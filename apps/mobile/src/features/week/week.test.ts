import { describe, expect, it } from 'vitest'
import { daysOfWeek, formatWeekRange, weekBar } from './week'

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

describe('formatWeekRange', () => {
  it('names the month once within a month', () => {
    expect(formatWeekRange(daysOfWeek(new Date(2026, 8, 24)), 'en-US')).toBe('Sep 20 – 26')
  })

  it('names both months across months', () => {
    expect(formatWeekRange(daysOfWeek(new Date(2026, 8, 30)), 'en-US')).toBe('Sep 27 – Oct 3')
  })
})

describe('weekBar', () => {
  it('tags today and greys out the days already past', () => {
    const days = weekBar(new Date(2026, 8, 24, 18, 30)) // Thursday, Sep 24
    expect(days.map(({ date, selectable, tag }) => [date, selectable, tag])).toEqual([
      ['2026-09-20', false, null],
      ['2026-09-21', false, null],
      ['2026-09-22', false, null],
      ['2026-09-23', false, null],
      ['2026-09-24', true, 'today'],
      ['2026-09-25', true, null],
      ['2026-09-26', true, null],
    ])
  })
})
