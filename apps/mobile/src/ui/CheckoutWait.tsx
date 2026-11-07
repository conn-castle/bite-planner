import { type ReactElement, useContext, useEffect } from 'react'
import type { GroceryOrder } from '../lib/api/types'
import { elapsed } from '../lib/format'
import { logger } from '../lib/log'
import { useNow } from '../lib/useNow'
import { FlowFrame, FooterSummary } from '../features/orders/FlowFrame'
import { FlowNotice } from '../features/orders/flowNotice'
import { stepIndex } from '../features/orders/steps'
import { useNotifiedOfJob } from '../features/notifications/notifications'
import { useSession } from '../features/session/session'
import { AgentWork } from './AgentWork'
import { PlateArt } from './art'
import { Button } from './Button'
import { Icon } from './Icon'
import { RetryNotice } from './RetryNotice'
import styles from './AgentWork.module.css'

interface CheckoutWaitProps {
  order: GroceryOrder
  step: 'delivery' | 'review'
  store: string
  failed: boolean
  /** It failed because Castle Chef is signed out of Instacart's website. */
  signedOut: boolean
  startFailed: string | null
  retrying: boolean
  onRetry: () => void
  onBack: () => void
  onClose: () => void
}

/** While the cart is filled and checkout read, or why it wasn't. */
export function CheckoutWait({ order, step, store, failed, signedOut, startFailed, retrying, onRetry, onBack, onClose }: CheckoutWaitProps): ReactElement {
  const now = useNow(1000)
  const job = order.quote?.current ? order.quote.job : undefined
  const reading = !failed && startFailed === null
  const userId: string | null = useSession().data?.user.id ?? null
  const notified: boolean = useNotifiedOfJob(reading ? (job?.requested_by.user_id ?? (retrying ? userId : null)) : null)
  // One problem on screen: the flow's own (the order couldn't be refreshed) comes first.
  const flowProblem = useContext(FlowNotice)

  // What went wrong is for the logs (the screen says it plainly): once per failed quote.
  const failedId = failed ? job?.id : undefined
  const failedError = failed ? (job?.error ?? null) : null
  useEffect(() => {
    if (failedId !== undefined) logger.error('orders.quote_failed', { orderId: order.id, jobId: failedId, error: failedError })
  }, [failedId, failedError, order.id])

  return (
    <FlowFrame
      step={stepIndex(step)}
      onBack={onBack}
      onClose={onClose}
      footer={
        <>
          <FooterSummary
            title={notified ? 'You can leave' : 'You can close this'}
            detail={notified ? "We'll notify you when the check finishes." : 'The draft is saved. Delivery times and your total will be here when you come back.'}
          />
          <Button variant="secondary" large onClick={onClose}>
            {notified ? <><Icon name="bell" /> Notify me</> : 'Close'}
          </Button>
        </>
      }
    >
      <AgentWork
        art={<PlateArt />}
        title={`Checking delivery and total at ${store}`}
        took={reading ? (job ? elapsed(new Date(job.created_at), now) : 'Starting') : null}
        lede="Checking delivery times and reading your estimated total. This can take a few minutes; anything already in your cart goes back afterwards."
      >
        {!flowProblem && !startFailed && failed && (
          <div className={styles.problem}>
            <RetryNotice
              message={
                signedOut
                  ? `Castle Chef is signed out of Instacart, so it couldn't check delivery. Sign it in again, then try again. Your order is saved.`
                  : `We couldn't check delivery and your total at ${store} this time. Your order is saved.`
              }
              onRetry={onRetry}
              busy={retrying}
            />
          </div>
        )}
        {!flowProblem && startFailed && (
          <div className={styles.problem}>
            <RetryNotice message={`Couldn't start it: ${startFailed}`} onRetry={onRetry} busy={retrying} />
          </div>
        )}
      </AgentWork>
    </FlowFrame>
  )
}

