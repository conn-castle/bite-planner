import { describe, expect, it } from 'vitest'
import { formatMinutes } from './facts'

describe('formatMinutes', () => {
  it('uses minutes under an hour, hours and minutes above', () => {
    expect(formatMinutes(0)).toBe('0 min')
    expect(formatMinutes(45)).toBe('45 min')
    expect(formatMinutes(60)).toBe('1 hr')
    expect(formatMinutes(70)).toBe('1 hr 10 min')
  })
})
