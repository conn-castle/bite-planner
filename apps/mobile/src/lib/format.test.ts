import { describe, expect, it } from 'vitest'
import { memberName } from './format'

describe('memberName', () => {
  const sam = { user_id: 'sam', name: 'Sam' }

  it('calls the signed-in member "You"', () => {
    expect(memberName(sam, 'sam')).toBe('You')
  })

  it('uses another member’s name, or a stand-in when they have none', () => {
    expect(memberName(sam, 'nick')).toBe('Sam')
    expect(memberName({ user_id: 'x', name: null }, 'nick')).toBe('Someone in your household')
  })
})
