import { useEffect, useRef, useState } from 'react'
import type { GroceryOrder, ShoppingItem } from '../../lib/api/types'
import { formatDate } from '../../lib/dates'
import { haptics } from '../../lib/haptics'
import { useNow } from '../../lib/useNow'
import { AgentWork } from '../../ui/AgentWork'
import { MealArt } from '../../ui/art'
import { Button } from '../../ui/Button'
import { Icon } from '../../ui/Icon'
import { InfoDialog } from '../../ui/InfoDialog'
import { Notice } from '../../ui/Notice'
import { ShoppingList } from '../../ui/ShoppingList'
import { Stats } from '../../ui/Stats'
import { Stepper } from '../../ui/Stepper'
import { useNotifiedOfJob } from '../notifications/notifications'
import { pickedMeals } from './batches'
import { shownDeliveryDate } from './dates'
import { FlowFrame, FooterSummary } from './FlowFrame'
import { elapsed, plural } from './format'
import { forMeals, itemAmount, listCounts, listState, quantityStep, splitList } from './list'
import styles from './ListStep.module.css'
import { useOrderSession } from './lock'
import { useEditsPending, useSetItem, useStartList } from './orders'
import { stepIndex } from './steps'

// The most of an item the stepper goes to.
const MAX_QUANTITY = 99

interface ListStepProps {
  order: GroceryOrder
  onBack: () => void
  onNext: () => void
  onClose: () => void
}

/**
 * The shopping list: waiting while it's made (it starts as the member leaves
 * Servings; here it starts if it hasn't), then every item to buy, by aisle,
 * each of which can be taken off or changed. Items assumed on hand are behind
 * a button.
 */
export function ListStep({ order, onBack, onNext, onClose }: ListStepProps) {
  const session = useOrderSession()
  const startList = useStartList()
  const { mutate: start } = startList
  const state = listState(order.shopping_list)

  // A list not started for these picks (e.g. starting failed offline, or picks
  // changed elsewhere) starts here, once.
  const started = useRef(false)
  useEffect(() => {
    if (state !== 'start' || started.current) return
    started.current = true
    start({ orderId: order.id, jobId: crypto.randomUUID(), session })
  }, [state, start, order.id, session])

  if (state === 'ready') return <ListReview order={order} onBack={onBack} onNext={onNext} onClose={onClose} />
  return (
    <ListWait
      order={order}
      state={state}
      onRetry={() => start({ orderId: order.id, jobId: crypto.randomUUID(), session })}
      startFailed={startList.isError ? startList.error.message : null}
      onBack={onBack}
      onClose={onClose}
    />
  )
}

interface ListWaitProps {
  order: GroceryOrder
  state: 'start' | 'building' | 'failed'
  onRetry: () => void
  startFailed: string | null
  onBack: () => void
  onClose: () => void
}

/** While the list is made, or why it wasn't. */
function ListWait({ order, state, onRetry, startFailed, onBack, onClose }: ListWaitProps) {
  const job = order.shopping_list?.job
  const now = useNow(1000)
  const building = state !== 'failed'
  const notified = useNotifiedOfJob(state === 'building' && job ? job.requested_by.user_id : null)
  const meals = pickedMeals(order).length

  return (
    <FlowFrame
      step={stepIndex('list')}
      onBack={onBack}
      onClose={onClose}
      footer={
        <>
          <FooterSummary
            title={notified ? 'You can leave' : 'You can close this'}
            detail={
              notified
                ? "We'll notify you when it's ready."
                : 'The draft is saved, and its list will be here when you come back.'
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
        art={<MealArt />}
        title="Making your shopping list"
        took={building ? (state === 'building' && job ? elapsed(new Date(job.created_at), now) : 'Starting') : null}
        lede={`Combining what your ${plural(meals, 'meal')} need into one list. This usually takes a minute or two.`}
      >
        {state === 'failed' && (
          <div className={styles.problem}>
            <Notice action={<button onClick={onRetry}>Try again</button>}>
              Couldn't make the list: {job?.error ?? 'it failed.'}
            </Notice>
          </div>
        )}
        {startFailed && (
          <div className={styles.problem}>
            <Notice action={<button onClick={onRetry}>Try again</button>}>Couldn't start: {startFailed}</Notice>
          </div>
        )}
      </AgentWork>
    </FlowFrame>
  )
}

interface ListReviewProps {
  order: GroceryOrder
  onBack: () => void
  onNext: () => void
  onClose: () => void
}

/** The list, ready: take items off (things you have), put them back, or change how much. */
function ListReview({ order, onBack, onNext, onClose }: ListReviewProps) {
  const session = useOrderSession()
  const setItem = useSetItem()
  const saving = useEditsPending()
  const [showOnHand, setShowOnHand] = useState(false)
  const meals = pickedMeals(order)
  const names = new Map(meals.map(({ meal }) => [meal.id, meal.name]))
  const { toBuy, onHand } = splitList(order.shopping_list?.items ?? [])
  const { ordered, removed } = listCounts(toBuy)
  const delivery = shownDeliveryDate(order)

  function change(item: ShoppingItem, change: { removed?: boolean; quantity?: number }) {
    haptics.tap()
    setItem.mutate({ orderId: order.id, itemId: item.id, session, ...change })
  }

  return (
    <FlowFrame
      step={stepIndex('list')}
      onBack={onBack}
      onClose={onClose}
      title="Shopping list"
      lede={`Built from your ${plural(meals.length, 'meal')}. Remove anything you already have.`}
      footer={
        <>
          <FooterSummary
            title={`${plural(ordered, 'item')} · ${plural(meals.length, 'meal')}`}
            detail={delivery ? `Arrives ${formatDate(delivery, 'medium')}` : ''}
          />
          <Button large onClick={onNext} disabled={saving || ordered === 0}>
            Next
          </Button>
        </>
      }
    >
      {setItem.isError && (
        <div className={styles.problem}>
          <Notice>Couldn't save that: {setItem.error.message}</Notice>
        </div>
      )}
      <Stats
        stats={[
          { value: ordered, label: 'to order' },
          { value: removed, label: 'removed' },
          { value: delivery ? formatDate(delivery, 'short') : '–', label: 'delivery' },
        ]}
      />
      {onHand.length > 0 && (
        <>
          <button type="button" className={`${styles.onHand} cc-hit`} onClick={() => setShowOnHand(true)}>
            <Icon name="info" />
            {plural(onHand.length, 'item')} assumed on hand
          </button>
          <InfoDialog isOpen={showOnHand} onClose={() => setShowOnHand(false)} icon="check" title="Assumed on hand">
            <p className={styles.onHandLede}>These recipes also use</p>
            <ul className={styles.onHandItems}>
              {onHand.map((item) => (
                <li key={item.id}>
                  <Icon name="check" />
                  {item.name}
                </li>
              ))}
            </ul>
            <p className={styles.onHandNote}>We assume every kitchen has them, so they're never on the list.</p>
          </InfoDialog>
        </>
      )}
      <ShoppingList
        items={toBuy}
        amount={itemAmount}
        forMeals={(item) => forMeals(item, names)}
        edit={{
          onRemove: (item, removed) => change(item, { removed }),
          onReset: (item) => item.recipe_quantity !== null && change(item, { quantity: item.recipe_quantity }),
          amountControl: (item, disabled) =>
            item.quantity !== null && (
              <Stepper
                inRow
                value={item.quantity}
                min={quantityStep(item)}
                max={MAX_QUANTITY}
                step={quantityStep(item)}
                disabled={disabled}
                format={(value) => itemAmount({ quantity: value, unit: item.unit })}
                label={`How much ${item.name}`}
                onChange={(quantity) => change(item, { quantity })}
              />
            ),
        }}
      />
    </FlowFrame>
  )
}
