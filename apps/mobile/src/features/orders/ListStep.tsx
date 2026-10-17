import { useContext, useEffect, useRef, useState } from 'react'
import type { GroceryOrder, ShoppingItem } from '../../lib/api/types'
import { elapsed, plural } from '../../lib/format'
import { haptics } from '../../lib/haptics'
import { useNow } from '../../lib/useNow'
import { AgentWork } from '../../ui/AgentWork'
import { PlateArt } from '../../ui/art'
import { Button } from '../../ui/Button'
import { Icon } from '../../ui/Icon'
import { InfoDialog } from '../../ui/InfoDialog'
import { Notice } from '../../ui/Notice'
import { RetryNotice } from '../../ui/RetryNotice'
import { logger } from '../../lib/log'
import { ShoppingList } from '../../ui/ShoppingList'
import { Stats } from '../../ui/Stats'
import { useNotifiedOfJob } from '../notifications/notifications'
import { pickedMeals } from './batches'
import { FlowFrame, FooterSummary } from './FlowFrame'
import { FlowNotice } from './flowNotice'
import { type ListState, listState, mealNames, productTotals, splitList, storeName, totalShown, undecided } from './list'
import styles from './ListStep.module.css'
import { useOrderSession } from './lock'
import { useEditsPending, useSetItem, useSetItemError, useShopList, useStartList } from './orders'
import { MAX_COUNT } from './ProductPage'
import { stepIndex } from './steps'
import { RecipeChanges } from './RecipeChanges'
import { recipeBlockers, unselectedChangeKey } from './recipeChangesState'

interface ListStepProps {
  /** The exact pending request set acknowledged when leaving Servings. */
  acknowledgedChanges?: string | null
  order: GroceryOrder
  onOpenMeal: (mealId: string, servings?: number) => void
  /** Opens an item's product (or, for one the store doesn't have, its alternatives) as a page. */
  onOpenProduct: (itemId: string) => void
  onBack: () => void
  onNext: () => void
  onClose: () => void
}

/**
 * The shopping list: waiting while it's made and shopped at the store (it starts
 * as the member leaves Servings; here it starts if it hasn't), then every item's
 * product, by aisle, each of which can be changed; the items the store doesn't
 * have come first, to decide on. Items assumed on hand are behind a button.
 */
export function ListStep({ order, onOpenMeal, onOpenProduct, onBack, onNext, onClose, acknowledgedChanges }: ListStepProps) {
  const session = useOrderSession()
  const startList = useStartList()
  const shopList = useShopList()
  const { mutate: start } = startList
  const { mutate: shop } = shopList
  const state = listState(order.shopping_list)
  const blockers = recipeBlockers(order).map((c) => `${c.job_id}:${c.state}`).sort().join(',')
  const warning = unselectedChangeKey(order)
  const [continued, setContinued] = useState<string | null>(null)
  const needsAcknowledgement = !!warning && continued !== warning && acknowledgedChanges !== warning

  // A list not started for these picks (e.g. starting failed offline, or picks
  // changed elsewhere) starts here, once; and one made but never shopped (e.g.
  // made before each list was shopped) is shopped, once.
  const started = useRef<ListState | null>(null)
  useEffect(() => { started.current = null }, [blockers, warning])
  useEffect(() => {
    if (blockers || needsAcknowledgement) return
    if ((state !== 'start' && state !== 'shopStart') || started.current === state) return
    started.current = state
    const request = { orderId: order.id, jobId: crypto.randomUUID(), session }
    if (state === 'start') start(request)
    else shop(request)
  }, [state, start, shop, order.id, session, blockers, warning, needsAcknowledgement])

  if (blockers || needsAcknowledgement) return <FlowFrame step={stepIndex('list')} title="Recipe changes" onBack={onBack} onClose={onClose}
    footer={<Button large disabled={!!blockers} onClick={() => setContinued(warning)}>{warning ? 'Start shopping anyway' : 'Start shopping'}</Button>}>
    <RecipeChanges order={order} onOpenMeal={onOpenMeal} />
    <RecipeChanges order={order} onOpenMeal={onOpenMeal} unselected />
    <Button variant="secondary" onClick={onBack}>Back to servings</Button>
  </FlowFrame>

  if (state === 'ready') {
    return <ListReview order={order} onOpenProduct={onOpenProduct} onBack={onBack} onNext={onNext} onClose={onClose} />
  }
  const failed = startList.isError ? startList.error.message : shopList.isError ? shopList.error.message : null
  return (
    <ListWait
      order={order}
      state={state}
      onRetry={() =>
        state === 'shopFailed' || state === 'shopStart'
          ? shop({ orderId: order.id, jobId: crypto.randomUUID(), session })
          : start({ orderId: order.id, jobId: crypto.randomUUID(), session })
      }
      retrying={startList.isPending || shopList.isPending}
      startFailed={failed}
      onBack={onBack}
      onClose={onClose}
    />
  )
}

interface ListWaitProps {
  order: GroceryOrder
  state: Exclude<ListState, 'ready'>
  onRetry: () => void
  retrying: boolean
  startFailed: string | null
  onBack: () => void
  onClose: () => void
}

/** While the list is made and shopped, or why it wasn't. */
function ListWait({ order, state, onRetry, retrying, startFailed, onBack, onClose }: ListWaitProps) {
  const list = order.shopping_list
  const shopping = state === 'shopStart' || state === 'shopping' || state === 'shopFailed'
  const job = shopping ? (list?.shop ?? undefined) : list?.job
  const now = useNow(1000)
  const working = state !== 'failed' && state !== 'shopFailed'
  const running = state === 'building' || state === 'shopping'
  const notified = useNotifiedOfJob(running && job ? job.requested_by.user_id : null)
  // One problem on screen: the flow's own (the order couldn't be refreshed) comes first.
  const flowProblem = useContext(FlowNotice)
  const meals = pickedMeals(order).length
  const store = storeName(order)

  // What went wrong is for the logs (the screen says it plainly): once per failed job.
  const failedJob = (state === 'failed' || state === 'shopFailed') && job?.status === 'failed' ? job : undefined
  const failedId = failedJob?.id
  const failedError = failedJob?.error ?? null
  useEffect(() => {
    if (failedId === undefined) return
    logger.error(shopping ? 'orders.shopping_failed' : 'orders.list_failed', {
      orderId: order.id,
      jobId: failedId,
      error: failedError,
    })
  }, [failedId, failedError, shopping, order.id])

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
        art={<PlateArt />}
        title={shopping ? `Shopping at ${store}` : 'Making your shopping list'}
        took={working ? (running && job ? elapsed(new Date(job.created_at), now) : 'Starting') : null}
        lede={
          shopping
            ? `Choosing ${store}'s products for your list. This can take several minutes.`
            : `Combining what your ${plural(meals, 'meal')} need into one list, then shopping it at ${store}. This can take several minutes.`
        }
      >
        {!flowProblem && !startFailed && (state === 'failed' || state === 'shopFailed') && (
          <div className={styles.problem}>
            <RetryNotice
              message={
                state === 'failed'
                  ? "We couldn't make your shopping list this time. Your meals and servings are saved."
                  : list?.address_problem ?? `We couldn't finish shopping at ${store} this time. Your list is saved.`
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

interface ListReviewProps {
  order: GroceryOrder
  onOpenProduct: (itemId: string) => void
  onBack: () => void
  onNext: () => void
  onClose: () => void
}

/**
 * The list, shopped: each item's product, with how many and a × to not buy it
 * (tap it for its page: its alternatives and details); the items the store
 * doesn't have come first, and each needs an alternative or skipping before going on.
 */
function ListReview({ order, onOpenProduct, onBack, onNext, onClose }: ListReviewProps) {
  const session = useOrderSession()
  const setItem = useSetItem()
  const setItemError = useSetItemError()
  const saving = useEditsPending()
  const [showOnHand, setShowOnHand] = useState(false)
  const list = order.shopping_list
  const store = storeName(order)
  const meals = pickedMeals(order)
  const names = new Map(meals.map(({ meal }) => [meal.id, meal.name]))
  const { toBuy, onHand } = splitList(list?.items ?? [])
  const totals = productTotals(toBuy)
  const toDecide = undecided(toBuy)

  function change(item: ShoppingItem, edit: { removed?: boolean; count?: number; alternative?: string }) {
    haptics.tap()
    setItem.mutate({ kind: 'item', orderId: order.id, itemId: item.id, session, ...edit })
  }

  return (
    <FlowFrame
      step={stepIndex('list')}
      onBack={onBack}
      onClose={onClose}
      notice={setItemError && <Notice>Couldn't save that: {setItemError.message}</Notice>}
      title="Shopping list"
      lede={`${store} products for your ${plural(meals.length, 'meal')}. Tap one for its alternatives.`}
      footer={
        <>
          <FooterSummary
            title={`${plural(totals.count, 'product')} · ${totalShown(totals)}`}
            detail={
              toDecide.length > 0
                ? `Decide on ${toDecide.map((item) => item.name).join(', ')} first`
                : 'Before fees and tip'
            }
          />
          <Button large onClick={onNext} disabled={saving || toDecide.length > 0 || totals.count === 0}>
            Next
          </Button>
        </>
      }
    >
      <Stats
        stats={[
          { value: totals.count, label: totals.count === 1 ? 'product' : 'products' },
          { value: totalShown(totals), label: totals.unpriced > 0 ? `before fees; ${totals.unpriced} unpriced` : 'before fees' },
          { value: toDecide.length, label: 'to decide' },
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
        forMeals={(item) => mealNames(item, names)}
        shop={{
          onOpen: (item) => onOpenProduct(item.id),
          edit: {
            onCount: (item, count) => change(item, { count }),
            onRemove: (item, removed) => change(item, { removed }),
            maxCount: MAX_COUNT,
          },
          missing: { store, onSkip: (item) => change(item, { removed: true }) },
        }}
      />
    </FlowFrame>
  )
}
