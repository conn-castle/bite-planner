import type { GroceryOrder, Job, MealSummary } from '../../lib/api/types'
import { haptics } from '../../lib/haptics'
import { useNow } from '../../lib/useNow'
import { MealArt } from '../../ui/art'
import { Button } from '../../ui/Button'
import { Icon } from '../../ui/Icon'
import { Notice } from '../../ui/Notice'
import { formatMinutes } from '../meals/facts'
import { newestBatchState, suggestions } from './batches'
import { FlowFrame, FooterSummary } from './FlowFrame'
import { elapsed, plural } from './format'
import { useEditsPending, useGenerateMeals, useSetMeal } from './orders'
import styles from './PickStep.module.css'

// Placeholder cards shown below the grid while more suggestions are made.
const PLACEHOLDER_CARDS = 4

interface PickStepProps {
  order: GroceryOrder
  onOpenMeal: (mealId: string) => void
  onNext: () => void
  onClose: () => void
}

/** Step 1: the suggestions in a grid, each added to the order or not, and more on request. */
export function PickStep({ order, onOpenMeal, onNext, onClose }: PickStepProps) {
  const setMeal = useSetMeal()
  // Moving on waits until every pick is saved, so the next steps see them all.
  const saving = useEditsPending()
  const picked = new Set(order.meal_ids)
  const count = order.meal_ids.length
  const running = newestBatchState(order).status === 'running'

  function toggle(meal: MealSummary) {
    haptics.tap()
    setMeal.mutate({ orderId: order.id, mealId: meal.id, picked: !picked.has(meal.id) })
  }

  return (
    <FlowFrame
      step={0}
      onClose={onClose}
      title="Pick meals"
      lede={`Add the meals you want in Order ${order.number}. Tap a meal to see its recipe.`}
      footer={
        <>
          <FooterSummary
            title={count ? `${plural(count, 'meal')} added` : 'No meals added yet'}
            detail={count ? `Covers about ${plural(count, 'day')}` : 'Tap Add on the meals you want'}
          />
          <Button large onClick={onNext} disabled={count === 0 || saving}>
            Next
          </Button>
        </>
      }
    >
      <p className={styles.note}>
        <Icon name="users" />
        Your picks are saved for the household as you go.
      </p>
      {setMeal.isError && (
        <div className={styles.notice}>
          <Notice>Couldn't save that change: {setMeal.error.message}</Notice>
        </div>
      )}
      <div className={styles.grid}>
        {suggestions(order).map((meal) => (
          <MealCard
            key={meal.id}
            meal={meal}
            added={picked.has(meal.id)}
            onToggle={() => toggle(meal)}
            onOpen={() => onOpenMeal(meal.id)}
          />
        ))}
        {running &&
          Array.from({ length: PLACEHOLDER_CARDS }, (_, index) => (
            <div key={index} className={`${styles.card} ${styles.placeholder}`} aria-hidden="true">
              <span className={styles.art} />
              <span className={styles.text}>
                <span className={styles.line} />
                <span className={`${styles.line} ${styles.short}`} />
              </span>
              <span className={styles.add} />
            </div>
          ))}
      </div>
      <MoreMeals order={order} />
    </FlowFrame>
  )
}

interface MealCardProps {
  meal: MealSummary
  added: boolean
  onToggle: () => void
  onOpen: () => void
}

function MealCard({ meal, added, onToggle, onOpen }: MealCardProps) {
  const minutes = meal.total_time_minutes ?? meal.active_time_minutes
  return (
    <div className={`${styles.card} ${added ? styles.added : ''}`}>
      <button type="button" className={styles.open} onClick={onOpen}>
        <span className={styles.art}>
          <MealArt />
        </span>
        <span className={styles.text}>
          <span className={styles.name}>{meal.name}</span>
          {minutes != null && <span className={styles.time}>{formatMinutes(minutes)}</span>}
        </span>
      </button>
      <button
        type="button"
        className={styles.add}
        onClick={onToggle}
        aria-pressed={added}
        aria-label={added ? `Remove ${meal.name}` : `Add ${meal.name}`}
      >
        <Icon name={added ? 'check' : 'plus'} />
        {added ? 'Added' : 'Add'}
      </button>
    </div>
  )
}

/** Below the grid: "Generate more", the batch being made, or why the last one failed. */
function MoreMeals({ order }: { order: GroceryOrder }) {
  const generate = useGenerateMeals()
  const batch = newestBatchState(order)
  const start = () => generate.mutate({ orderId: order.id, jobId: crypto.randomUUID() })

  if (batch.status === 'running') return <MoreRunning job={batch.job} />
  return (
    <div className={styles.more}>
      {batch.status === 'failed' && (
        <Notice action={<button onClick={start} disabled={generate.isPending}>Try again</button>}>
          Couldn't come up with more meals: {batch.job.error ?? 'the batch failed.'}
        </Notice>
      )}
      {generate.isError && (
        <Notice>Couldn't start more meals: {generate.error.message}</Notice>
      )}
      {batch.status !== 'failed' && (
        <>
          <Button variant="secondary" block onClick={start} disabled={generate.isPending}>
            <Icon name="spark" />
            Generate more
          </Button>
          <p className={styles.hint}>20 more ideas below · takes a minute or two</p>
        </>
      )}
    </div>
  )
}

function MoreRunning({ job }: { job: Job }) {
  const now = useNow(1000)
  return (
    <div className={styles.more} aria-busy="true">
      <span className={styles.progress} aria-hidden="true" />
      <p className={styles.hint}>
        <b>Generating 20 more</b> · {elapsed(new Date(job.created_at), now)}
      </p>
      <p className={styles.hint}>You can close this; they'll be here when you come back.</p>
    </div>
  )
}
