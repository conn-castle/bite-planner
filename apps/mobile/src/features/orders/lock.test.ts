import { describe, expect, it } from 'vitest'
import type { OrderLock } from '../../lib/api/types'
import { lockHolder, lockRequest } from './lock'

const lock = (userId: string, session: string | null): OrderLock => ({
  member: { user_id: userId, name: userId === 'me' ? 'Me' : 'Sam' },
  session,
  since: '2026-09-26T09:00:00Z',
  step: 'pick',
  expires_at: '2026-09-26T09:02:00Z',
})

describe('lockHolder', () => {
  it("tells this session's lock from this member's other phone's and another member's", () => {
    expect(lockHolder(null, 'phone', 'me')).toBe('none')
    expect(lockHolder(lock('me', 'phone'), 'phone', 'me')).toBe('this')
    expect(lockHolder(lock('me', 'ipad'), 'phone', 'me')).toBe('you elsewhere')
    expect(lockHolder(lock('sam', null), 'phone', 'me')).toBe('someone else')
  })
})

describe('lockRequest', () => {
  it('renews the lock this session has', () => {
    expect(lockRequest('this', true)).toBe('renew')
  })

  it("takes it when it's free", () => {
    expect(lockRequest('none', false)).toBe('take')
    // Released or lapsed after this session had it, with no one else taking it.
    expect(lockRequest('none', true)).toBe('take')
  })

  it("takes this member's own lock from before as the flow opens, but not back from their other phone", () => {
    expect(lockRequest('you elsewhere', false)).toBe('take')
    expect(lockRequest('you elsewhere', true)).toBeNull()
  })

  it("never takes another member's, or asks about a placed order", () => {
    expect(lockRequest('someone else', false)).toBeNull()
    expect(lockRequest(null, false)).toBeNull()
  })
})
