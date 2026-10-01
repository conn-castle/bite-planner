import { App } from '@capacitor/app'
import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { apiRequest } from '../../lib/api/client'
import { ApiError } from '../../lib/api/errors'
import type { GroceryOrder, OrderLock, OrderStep } from '../../lib/api/types'
import { errorFields, logger } from '../../lib/log'
import { queryKeys } from '../../lib/query/keys'
import { LOCK_RENEW_MS, type LockedOut, lockHolder, lockRequest, withLock } from './lock'
import { FlowClosed, takeLock } from './orders'

interface OrderLockState {
  lockedOut: LockedOut
  /** Takes the lock back from this member's other phone. */
  takeOver: () => void
}

/**
 * Keeps the flow's order locked to its session while it shows a draft: takes
 * the lock when it's free (or still this member's from before), renews it every
 * 30 seconds, at once when the step changes or the app comes back to the
 * foreground, and gives up when it's gone (the order is fetched again to show
 * why). `order` is undefined until known, or while it can't be trusted (the
 * cached copy shown while opening).
 */
export function useOrderLock(
  order: GroceryOrder | undefined,
  session: string,
  userId: string | undefined,
  step: OrderStep,
): OrderLockState {
  const queryClient = useQueryClient()
  const orderId = order?.id
  const holder = order?.status === 'draft' ? lockHolder(order.lock, session, userId) : null
  // Once this session has had the lock, losing it to this member's other phone
  // shows the locked screen rather than taking it straight back.
  const [sessionWithLock, setSessionWithLock] = useState<string | null>(null)
  if (holder === 'this' && sessionWithLock !== session) setSessionWithLock(session)
  const hadLock = sessionWithLock === session

  const latest = useRef({ orderId, holder, hadLock, step })
  useEffect(() => {
    latest.current = { orderId, holder, hadLock, step }
  }, [orderId, holder, hadLock, step])
  const sending = useRef(false)
  // The order was placed or discarded (409, 404): nothing more to send about it.
  const ended = useRef(false)

  const send = useCallback(
    async (how: 'take' | 'renew') => {
      const { orderId, step } = latest.current
      if (orderId === undefined || sending.current) return
      sending.current = true
      try {
        if (how === 'take') {
          // After the changes still being saved, and before a release as the flow closes.
          await takeLock(queryClient, { orderId, session, step })
          logger.info('orders.lock_taken', { orderId })
        } else {
          const lock = await apiRequest<OrderLock>('PUT', `/v1/grocery-orders/${orderId}/lock`, { step }, session)
          queryClient.setQueryData<GroceryOrder>(queryKeys.order(orderId), (cached) => cached && withLock(cached, lock))
        }
      } catch (error) {
        if (error instanceof FlowClosed) return
        if (error instanceof ApiError && [404, 409, 423].includes(error.status)) {
          // Someone else has it (423), or the order was placed or discarded: show why.
          logger.info('orders.lock_lost', { orderId, how, status: error.status })
          if (error.status !== 423) ended.current = true
          void queryClient.invalidateQueries({ queryKey: queryKeys.order(orderId) })
        } else {
          // Offline, say: the next tick tries again.
          logger.warn('orders.lock_failed', { orderId, how, ...errorFields(error) })
        }
      } finally {
        sending.current = false
      }
    },
    [queryClient, session],
  )

  const tick = useCallback(() => {
    if (ended.current) return
    const { holder, hadLock } = latest.current
    const request = lockRequest(holder, hadLock)
    if (request) void send(request)
  }, [send])

  // At once when who has it or the step changes; then every 30 seconds, and on
  // coming back to the foreground (iOS stops timers while the app is away).
  useEffect(() => {
    tick()
  }, [holder, step, tick])
  useEffect(() => {
    const timer = setInterval(tick, LOCK_RENEW_MS)
    const listener = App.addListener('appStateChange', ({ isActive }) => {
      if (isActive) tick()
    })
    return () => {
      clearInterval(timer)
      void listener.then((handle) => handle.remove())
    }
  }, [tick])

  const lock = order?.lock ?? null
  const lockedOut: LockedOut =
    lock && holder === 'someone else'
      ? { by: 'someone else', lock }
      : lock && holder === 'you elsewhere' && hadLock
        ? { by: 'you elsewhere', lock }
        : null
  return { lockedOut, takeOver: () => void send('take') }
}
