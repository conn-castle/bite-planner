import { afterEach, describe, expect, it, vi } from 'vitest'
import { formatDate, localDate } from './dates'

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
