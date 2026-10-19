import { describe, expect, it } from 'vitest'
import type { GroceryOrder, OrderLock, OrderStep } from '../../lib/api/types'
import { lockHolder, lockRequest, withLock } from './lock'

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

describe('withLock', () => {
  const draft = (reached: OrderStep): GroceryOrder => ({
    book_state: null,
    book_on: null,
    booking: null,
    book_issue: null, can_confirm_absent: false,
    id: 'draft',
    number: 4,
    status: 'draft',
    created_by: { user_id: 'me', name: 'Me' },
    created_at: '2026-09-24T09:00:00Z',
    target_date: null,
    decide_by_date: null,
    reminders_start_date: null,
    earliest_delivery_date: '2026-09-25',
    delivery_time_zone: 'America/New_York',
    delivery_date: null,
    delivery_by: null,

    next_delivery_date: null,
    recipe_changes: [], recipes_editable: true, reached_step: reached,
    jobs: [],
    picks: [],
    lock: null,
    shopping_list: null,
    quote: null,
  })
  const at = (step: OrderStep): OrderLock => ({ ...lock('me', 'phone'), step })

  it('keeps the copy as far along as the server: a step the lock reaches is one the flow has reached', () => {
    expect(withLock(draft('list'), at('delivery'))).toMatchObject({ lock: at('delivery'), reached_step: 'delivery' })
  })

  it('keeps the furthest step when the member goes back', () => {
    expect(withLock(draft('review'), at('servings'))).toMatchObject({ lock: at('servings'), reached_step: 'review' })
  })
})
