import { useContext } from 'react'
import { BATCH_SIZE } from '../../lib/api/limits'
import type { GroceryOrder, Job, MealSummary } from '../../lib/api/types'
import { elapsed, plural } from '../../lib/format'
import { haptics } from '../../lib/haptics'
import { usePressIntent } from '../../lib/usePressIntent'
import { useNow } from '../../lib/useNow'
import { MealArt } from '../../ui/art'
import { Button } from '../../ui/Button'
import { CornerToggle } from '../../ui/CornerToggle'
import { Icon } from '../../ui/Icon'
import { Note } from '../../ui/Note'
import { Notice } from '../../ui/Notice'
import { Stats } from '../../ui/Stats'
import { usePrefetchMeal } from '../meals/cached'
import { newestBatchState, suggestions } from './batches'
import { FlowFrame, FooterSummary } from './FlowFrame'
import { FlowNotice } from './flowNotice'
import { useEditsPending, useGenerateMeals, useSetNotForUs } from './orders'
import { useOrderSession } from './lock'
import styles from './PickStep.module.css'
import { stepIndex } from './steps'
import { useToggleMeal } from './useToggleMeal'
import { AddMealButton } from '../../ui/AddMealButton'

// Placeholder cards shown below the grid while more suggestions are made.
const PLACEHOLDER_CARDS = 4

interface PickStepProps {
  order: GroceryOrder
  /** Opens a recipe, as written: servings are chosen in the next step. */
  onOpenMeal: (mealId: string) => void
  onNext: () => void
  onClose: () => void
}

/** Step 1: the suggestions in a grid, each added to the order or not, and more on request. */
export function PickStep({ order, onOpenMeal, onNext, onClose }: PickStepProps) {
  const session = useOrderSession()
  const selection = useToggleMeal(order)
  const setMeal = selection.mutation
  const setNotForUs = useSetNotForUs()
  // The newest change that failed: a pick, or a ✕.
  const failed = [setMeal, setNotForUs]
    .filter((change) => change.isError)
    .sort((a, b) => b.submittedAt - a.submittedAt)[0]?.error
  // Moving on waits until every pick is saved, so the next steps see them all.
  const saving = useEditsPending()
  const picked = new Set(order.picks.map((pick) => pick.meal_id))
  const count = order.picks.length
  const running = newestBatchState(order).status === 'running'

  function dismiss(meal: MealSummary) {
    haptics.tap()
    setNotForUs.mutate({
      kind: 'not-for-us',
      orderId: order.id,
      mealId: meal.id,
      notForUs: meal.rating !== 'not_for_us',
      session,
    })
  }

  return (
    <FlowFrame
      step={stepIndex('pick')}
      onClose={onClose}
      notice={
        selection.problem ? (
          <Notice>Couldn't save that change: {selection.problem}</Notice>
        ) : failed ? (
          <Notice>Couldn't save that change: {failed.message}</Notice>
        ) : null
      }
      title="Pick meals"
      lede={`Add the meals you want in Order ${order.number}. Tap ✕ on anything you don't want: Chef stops suggesting meals like it to your household.`}
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
      <Note icon="users">Your picks are saved for the household as you go.</Note>
      <div className={styles.grid}>
        {suggestions(order).map((meal) => (
          <MealCard
            key={meal.id}
            meal={meal}
            added={picked.has(meal.id)}
            change={order.recipe_changes.find((change) => change.meal_id === meal.id)?.state}
            onToggle={() => void selection.toggle(meal)}
            onDismiss={() => dismiss(meal)}
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
  change?: 'running' | 'ready' | 'failed'
  onToggle: () => void
  onDismiss: () => void
  onOpen: () => void
}

/**
 * A suggestion: open its recipe, add it, or ✕ it ("don't show meals like
 * this"), which also takes it out of the order and can be undone.
 */
function MealCard({ meal, added, change, onToggle, onDismiss, onOpen }: MealCardProps) {
  const prefetch = usePrefetchMeal()
  const press = usePressIntent(() => prefetch(meal.id))
  // A meal marked "not for us" is never picked (see edits.ts), so it's never also added.
  const notForUs = meal.rating === 'not_for_us'

  return (
    <div className={`${styles.card} ${added ? styles.added : ''} ${notForUs ? styles.notForUs : ''}`}>
      <button type="button" className={styles.open} onClick={onOpen} {...press}>
        <span className={styles.art}>
          <MealArt meal={meal} />
        </span>
        {notForUs && <span className={styles.gone}>No more meals like this</span>}
        <span className={styles.text}>
          <span className={styles.name}>{meal.name}</span>
          <Stats
            variant="meal"
            className={styles.facts}
            stats={[
              { icon: 'ingredients', value: meal.ingredient_count, label: 'Ingredients' },
              { icon: 'clock', value: meal.total_time_minutes, unit: 'min', label: 'Total', missingLabel: 'Total time not listed' },
              { icon: 'person', value: meal.active_time_minutes, unit: 'min', label: 'Hands-on', missingLabel: 'Hands-on time not listed' },
            ]}
          />
        </span>
      </button>
      <CornerToggle
        off={notForUs}
        offIcon="undo"
        label={notForUs ? `Show meals like ${meal.name} again` : `Don't show meals like ${meal.name}`}
        onToggle={onDismiss}
      />
      {change && <span className={styles.change}>{change === 'running' ? 'Changing recipe…' : change === 'ready' ? 'Changes to review' : "Recipe change failed"}</span>}
      <AddMealButton compact name={meal.name} added={added} disabled={notForUs} onToggle={onToggle} className={styles.add} />
    </div>
  )
}

/** Below the grid: "Generate more", the batch being made, or why the last one failed. */
function MoreMeals({ order }: { order: GroceryOrder }) {
  const session = useOrderSession()
  const generate = useGenerateMeals()
  const batch = newestBatchState(order)
  // One problem on screen: the flow's own (the order couldn't be refreshed) comes first.
  const flowProblem = useContext(FlowNotice)
  const start = () => generate.mutate({ orderId: order.id, jobId: crypto.randomUUID(), session })

  if (batch.status === 'running') return <MoreRunning job={batch.job} />
  return (
    <div className={styles.more}>
      {!flowProblem && !generate.isError && batch.status === 'failed' && (
        <Notice action={<button onClick={start} disabled={generate.isPending}>Try again</button>}>
          Couldn't come up with more meals: {batch.job.error ?? 'the batch failed.'}
        </Notice>
      )}
      {!flowProblem && generate.isError && (
        <Notice>Couldn't start more meals: {generate.error.message}</Notice>
      )}
      {batch.status !== 'failed' && (
        <>
          <Button variant="secondary" block onClick={start} disabled={generate.isPending}>
            <Icon name="spark" />
            Generate more
          </Button>
          <p className={styles.hint}>{BATCH_SIZE} more ideas below · takes a minute or two</p>
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
        <b>Generating {BATCH_SIZE} more</b> · {elapsed(new Date(job.created_at), now)}
      </p>
      <p className={styles.hint}>You can close this; they'll be here when you come back.</p>
    </div>
  )
}
