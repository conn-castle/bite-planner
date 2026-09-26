import type { GroceryOrder } from '../../lib/api/types'
import { useNow } from '../../lib/useNow'
import { MealArt } from '../../ui/art'
import { Button } from '../../ui/Button'
import { Notice } from '../../ui/Notice'
import { ProgressBar } from '../../ui/ProgressBar'
import { RatePrompt } from '../meals/RatePrompt'
import { newestBatchState } from './batches'
import { FlowFrame, FooterSummary } from './FlowFrame'
import { elapsed } from './format'
import { useGenerateMeals } from './orders'
import styles from './WaitStep.module.css'

interface WaitStepProps {
  order: GroceryOrder
  onClose: () => void
}

/**
 * Before there are any suggestions: the first batch being made (a minute or
 * two, and the flow can be closed meanwhile), or why it failed.
 */
export function WaitStep({ order, onClose }: WaitStepProps) {
  const generate = useGenerateMeals()
  const batch = newestBatchState(order)
  const now = useNow(1000)
  const start = () => generate.mutate({ orderId: order.id, jobId: crypto.randomUUID() })
  const running = batch.status === 'running' || generate.isPending

  return (
    <FlowFrame
      step={0}
      onClose={onClose}
      footer={
        <>
          <FooterSummary
            title="You can close this"
            detail={
              running
                ? 'The draft is saved, and its meal ideas will be here when you come back.'
                : 'The draft is saved for the household.'
            }
          />
          <Button variant="secondary" large onClick={onClose}>
            Close
          </Button>
        </>
      }
    >
      <div className={styles.wait}>
        <div className={`${styles.orb} ${running ? styles.spinning : ''}`} aria-hidden="true">
          <MealArt />
        </div>
        <h2 className={styles.title}>Finding meals for Order {order.number}</h2>
        {running && (
          <>
            <p className={styles.lede}>This usually takes a minute or two.</p>
            <ProgressBar className={styles.progress} />
            <p className={styles.elapsed} role="timer">
              {batch.status === 'running' ? elapsed(new Date(batch.job.created_at), now) : 'Starting'}
            </p>
            <div className={styles.rate}>
              <RatePrompt />
            </div>
          </>
        )}
        {!running && batch.status === 'failed' && (
          <div className={styles.problem}>
            <Notice action={<button onClick={start}>Try again</button>}>
              Couldn't come up with meals: {batch.job.error ?? 'the batch failed.'}
            </Notice>
          </div>
        )}
        {!running && batch.status !== 'failed' && (
          <div className={styles.action}>
            <Button onClick={start}>Get meal ideas</Button>
          </div>
        )}
        {generate.isError && (
          <div className={styles.problem}>
            <Notice>Couldn't start: {generate.error.message}</Notice>
          </div>
        )}
      </div>
    </FlowFrame>
  )
}
