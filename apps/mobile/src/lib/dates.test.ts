import { afterEach, describe, expect, it, vi } from 'vitest'
import { addDays, daysBetween, formatDate, localDate, relativeDay } from './dates'

// Time zones are set with vi.stubEnv('TZ', ...), which Node applies at once.
afterEach(() => {
  vi.unstubAllEnvs()
})

describe('formatDate', () => {
  it('shows a delivery date as that calendar day in every time zone', () => {
    for (const zone of ['Pacific/Honolulu', 'America/New_York', 'UTC', 'Pacific/Auckland']) {
      vi.stubEnv('TZ', zone)
      expect(formatDate('2026-09-28', 'short', 'en-US')).toBe('Mon 28')
      expect(formatDate('2026-09-28', 'medium', 'en-US')).toBe('Mon, Sep 28')
      expect(formatDate('2026-09-28', 'long', 'en-US')).toBe('Monday, Sep 28')
    }
  })
})

describe('localDate', () => {
  it('is the calendar day on the phone, not in UTC', () => {
    vi.stubEnv('TZ', 'America/New_York')
    // 11:30 PM on Sep 25 in New York is already Sep 26 in UTC.
    expect(localDate(new Date('2026-09-26T03:30:00Z'))).toBe('2026-09-25')
  })
})

describe('addDays and daysBetween', () => {
  it('count whole calendar days, across a daylight saving change', () => {
    vi.stubEnv('TZ', 'America/New_York')
    expect(addDays('2026-10-31', 2)).toBe('2026-11-02')
    expect(daysBetween('2026-10-31', '2026-11-02')).toBe(2)
    expect(daysBetween('2026-11-02', '2026-10-31')).toBe(-2)
  })
})

describe('relativeDay', () => {
  const today = '2026-09-24' // a Thursday

  it('says today, tomorrow, the weekday within the week, then the date', () => {
    expect(relativeDay(today, today, 'long', 'en-US')).toBe('today')
    expect(relativeDay('2026-09-25', today, 'long', 'en-US')).toBe('tomorrow')
    expect(relativeDay('2026-09-26', today, 'long', 'en-US')).toBe('Saturday')
    expect(relativeDay('2026-09-30', today, 'long', 'en-US')).toBe('Wednesday')
    expect(relativeDay('2026-10-01', today, 'long', 'en-US')).toBe('Thu, Oct 1')
  })

  it('says yesterday and short weekdays in the short style', () => {
    expect(relativeDay('2026-09-23', today, 'short', 'en-US')).toBe('yesterday')
    expect(relativeDay('2026-09-26', today, 'short', 'en-US')).toBe('Sat')
    expect(relativeDay('2026-09-22', today, 'long', 'en-US')).toBe('Tue, Sep 22')
  })
})
