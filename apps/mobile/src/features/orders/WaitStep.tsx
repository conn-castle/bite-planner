import { useContext, useEffect } from 'react'
import type { GroceryOrder } from '../../lib/api/types'
import { elapsed } from '../../lib/format'
import { useNow } from '../../lib/useNow'
import { AgentWork } from '../../ui/AgentWork'
import { PlateArt } from '../../ui/art'
import { Button } from '../../ui/Button'
import { Icon } from '../../ui/Icon'
import { RetryNotice } from '../../ui/RetryNotice'
import { logger } from '../../lib/log'
import { RatePrompt } from '../meals/RatePrompt'
import { useNotifiedOfJob } from '../notifications/notifications'
import { useSession } from '../session/session'
import { newestBatchState } from './batches'
import { FlowFrame, FooterSummary } from './FlowFrame'
import { FlowNotice } from './flowNotice'
import { useGenerateMeals } from './orders'
import { useOrderSession } from './lock'
import styles from './WaitStep.module.css'
import { stepIndex } from './steps'

interface WaitStepProps {
  order: GroceryOrder
  onClose: () => void
}

/**
 * Before there are any suggestions: the first batch being made (a minute or
 * two, and the flow can be closed meanwhile), or why it failed.
 */
export function WaitStep({ order, onClose }: WaitStepProps) {
  const session = useOrderSession()
  const generate = useGenerateMeals()
  const batch = newestBatchState(order)
  const now = useNow(1000)
  const start = () => generate.mutate({ orderId: order.id, jobId: crypto.randomUUID(), session })
  const running = batch.status === 'running' || generate.isPending
  // "Ideas ready" reaches this phone only for its own batch, with notifications on.
  const userId = useSession().data?.user.id ?? null
  // Who started the batch being made (this member, when it was just asked for here).
  const startedBy = batch.status === 'running' ? batch.job.requested_by.user_id : generate.isPending ? userId : null
  const notified = useNotifiedOfJob(running ? startedBy : null)
  // One problem on screen: the flow's own (the order couldn't be refreshed) comes first.
  const flowProblem = useContext(FlowNotice)
  // What went wrong is for the logs (the screen says it plainly): once per failed batch.
  const failedId = batch.status === 'failed' ? batch.job.id : undefined
  const failedError = batch.status === 'failed' ? batch.job.error : null
  useEffect(() => {
    if (failedId !== undefined) logger.error('orders.batch_failed', { orderId: order.id, jobId: failedId, error: failedError })
  }, [failedId, failedError, order.id])

  return (
    <FlowFrame
      step={stepIndex('pick')}
      onClose={onClose}
      footer={
        <>
          <FooterSummary
            title={notified ? 'You can leave' : 'You can close this'}
            detail={
              notified
                ? "The card on Home keeps working and we'll notify you."
                : running
                  ? 'The draft is saved, and its meal ideas will be here when you come back.'
                  : 'The draft is saved for the household.'
            }
          />
          <Button variant="secondary" large onClick={onClose}>
            {notified ? (
              <>
                <Icon name="bell" />
                Notify me
              </>
            ) : (
              'Close'
            )}
          </Button>
        </>
      }
    >
      <AgentWork
        art={<PlateArt />}
        title={`Finding meals for Order ${order.number}`}
        took={running ? (batch.status === 'running' ? elapsed(new Date(batch.job.created_at), now) : 'Starting') : null}
        lede="This usually takes a minute or two."
      >
        {running && (
          <div className={styles.rate}>
            <RatePrompt />
          </div>
        )}
        {!flowProblem && !generate.isError && !running && batch.status === 'failed' && (
          <div className={styles.problem}>
            <RetryNotice
              message="We couldn't come up with meal ideas this time."
              onRetry={start}
              busy={generate.isPending}
            />
          </div>
        )}
        {!running && batch.status !== 'failed' && (
          <div className={styles.action}>
            <Button onClick={start}>Get meal ideas</Button>
          </div>
        )}
        {!flowProblem && generate.isError && (
          <div className={styles.problem}>
            <RetryNotice message={`Couldn't start it: ${generate.error.message}`} onRetry={start} busy={generate.isPending} />
          </div>
        )}
      </AgentWork>
    </FlowFrame>
  )
}
