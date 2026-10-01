import { createContext, useContext } from 'react'
import type { GroceryOrder, OrderLock } from '../../lib/api/types'
import { stepIndex } from './steps'

/**
 * The order lock: only one ordering session (one opening of the order flow, on
 * one phone) changes the household's draft at a time. Opening the flow takes
 * the lock unless another member has it; while the flow is open the app renews
 * it every 30 seconds, and it lapses 2 minutes after the last renewal
 * (backend orders.py). Everyone else sees who is ordering, and where.
 */

// The server's lock lasts 2 minutes after each renewal.
export const LOCK_RENEW_MS = 30_000

/** The ordering session of the order flow that's open; changes it makes are sent with it. */
export const OrderSession = createContext<string | null>(null)

export function useOrderSession(): string {
  const session = useContext(OrderSession)
  if (session === null) throw new Error('Only the order flow changes a draft.')
  return session
}

/**
 * Who has the order's lock, as an ordering session sees it: this session, no
 * one, this member on another phone (or this one before a relaunch), or
 * another member.
 */
export type LockHolder = 'this' | 'none' | 'you elsewhere' | 'someone else'

export function lockHolder(lock: OrderLock | null, session: string, userId: string | undefined): LockHolder {
  if (lock === null) return 'none'
  if (lock.session === session) return 'this'
  return lock.member.user_id === userId ? 'you elsewhere' : 'someone else'
}

/**
 * What the open flow sends about the lock: renew its own; take it when it's
 * free, or still this member's from before (another phone, or this one before
 * a relaunch) and this session hasn't had it yet; otherwise nothing (someone
 * else has it, or this member's other phone took it over).
 */
export function lockRequest(holder: LockHolder | null, hadLock: boolean): 'renew' | 'take' | null {
  if (holder === 'this') return 'renew'
  if (holder === 'none' || (holder === 'you elsewhere' && !hadLock)) return 'take'
  return null
}

/** Whether another member is placing the next order, as Home and the order view show it. */
export function lockedByOther(order: GroceryOrder | null | undefined, userId: string | undefined): OrderLock | null {
  const lock = order?.lock ?? null
  return lock && lock.member.user_id !== userId ? lock : null
}

/** Why the flow can't change the order: another member has it, or this member on another phone. */
export type LockedOut = { by: 'someone else' | 'you elsewhere'; lock: OrderLock } | null

/**
 * The order with the lock the server just gave or renewed for this session: the
 * step the lock is at is one its flow has reached, so the copy kept stays as far
 * along as the server's (where the flow opens again).
 */
export function withLock(order: GroceryOrder, lock: OrderLock): GroceryOrder {
  const reached = stepIndex(lock.step) > stepIndex(order.reached_step) ? lock.step : order.reached_step
  return { ...order, lock, reached_step: reached }
}
