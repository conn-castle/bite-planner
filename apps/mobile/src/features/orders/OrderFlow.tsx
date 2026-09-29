import { IonModal, IonNav } from '@ionic/react'
import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../../lib/api/errors'
import { localDate } from '../../lib/dates'
import { logger } from '../../lib/log'
import type { GroceryOrder, OrderStep as Step } from '../../lib/api/types'
import { useNow } from '../../lib/useNow'
import { Button } from '../../ui/Button'
import { Notice } from '../../ui/Notice'
import { Skeleton } from '../../ui/Skeleton'
import { MealPage } from '../meals/MealPage'
import { loadingIdeas, setOrderFlowOpen, stopAwaitingIdeas, useAwaitedIdeas } from '../notifications/notifications'
import { useSession } from '../session/session'
import { suggestions } from './batches'
import { shownDeliveryDate, shownNextDeliveryDate } from './dates'
import { DeliveryStep } from './DeliveryStep'
import { FlowFrame } from './FlowFrame'
import { FlowNotice } from './flowNotice'
import { OrderSession, useOrderSession } from './lock'
import { stepShown } from './steps'
import { useOrderLock } from './useOrderLock'
import { LockedStep } from './LockedStep'
import styles from './OrderFlow.module.css'
import { ListStep } from './ListStep'
import { closeFlowSession, nextQuery, useNextOrder, useOpenOrder, useOrder, useStartList } from './orders'
import { NextDeliveryStep } from './NextDeliveryStep'
import { PickStep } from './PickStep'
import { PlacedStep } from './PlacedStep'
import { ProductPage } from './ProductPage'
import { ReviewStep } from './ReviewStep'
import { ServingsStep } from './ServingsStep'
import { WaitStep } from './WaitStep'

// How often the flow checks whether the phone's calendar day has changed.
const DAY_CHECK_MS = 60_000

interface OrderFlowModalProps {
  isOpen: boolean
  onDismiss: () => void
  /**
   * Opens this order as it is, rather than the household's draft (made if there's
   * none): a notification's, which must never start a new one.
   */
  orderId?: string
  /** The step it opens at (a notification's: the shopping list); picking meals otherwise. */
  startAt?: Step
}

/**
 * The create-order flow, full-screen over the app (a review decision over the
 * mockup's card: it's a long task, and a swipe mustn't close it). Closing it
 * (Close) keeps the household's draft to resume later, and lets the rest of the
 * household order. A recipe or a product opens inside it, on its own navigation
 * stack. Each opening is an ordering session of its own (lock.ts).
 */
export function OrderFlowModal({ isOpen, onDismiss, orderId, startAt }: OrderFlowModalProps) {
  const queryClient = useQueryClient()
  const modal = useRef<HTMLIonModalElement>(null)
  const nav = useRef<HTMLIonNavElement>(null)
  const [session, setSession] = useState(() => crypto.randomUUID())
  // The order the flow shows, whose lock closing it releases.
  const shownOrder = useRef<string | undefined>(undefined)

  // While it's open, "ideas ready" needs no toast: the ideas show here.
  useEffect(() => {
    setOrderFlowOpen(isOpen)
    return () => setOrderFlowOpen(false)
  }, [isOpen])

  // Stable while open, or the nav would start over: it resets whenever its root
  // changes (the order it opens changes only as it opens, or for a new notification).
  const root = useCallback(
    () => (
      <OrderSession.Provider value={session}>
        <OrderFlow
          orderId={orderId}
          startAt={startAt}
          session={session}
          onShown={(id) => (shownOrder.current = id)}
          onClose={() => void modal.current?.dismiss()}
          onOpenMeal={(mealId, servings) => void openMeal(nav.current, mealId, servings)}
          onOpenProduct={(id, itemId) => void openProduct(nav.current, session, id, itemId)}
        />
      </OrderSession.Provider>
    ),
    [orderId, startAt, session],
  )

  function dismissed() {
    closeFlowSession(queryClient, session, shownOrder.current)
    shownOrder.current = undefined
    // The next opening is a session of its own.
    setSession(crypto.randomUUID())
    stopAwaitingIdeas('closed')
    // The flow kept its own copy of the order current; the next order's may be out of date.
    void queryClient.invalidateQueries({ queryKey: nextQuery.queryKey })
    onDismiss()
  }

  return (
    <IonModal ref={modal} className="cc-full-screen" isOpen={isOpen} onDidDismiss={dismissed}>
      <IonNav ref={nav} root={root} />
    </IonModal>
  )
}

/** Pushes a recipe onto the flow's nav: at a pick's servings, when it's one. */
async function openMeal(nav: HTMLIonNavElement | null, mealId: string, servings: number | undefined) {
  if (!nav) return
  // A meal not picked yet has no servings of its own to show or change.
  await nav.push(() => <MealPage mealId={mealId} servings={servings} suggestion={servings === undefined} />)
  // A recipe once opened as a blank card (its page left invisible); report it if that happens again.
  const page = (await nav.getActive())?.element
  if (page && getComputedStyle(page).opacity === '0') {
    logger.error('orders.recipe_not_shown', { mealId, pageClasses: page.className })
  }
}

/** Pushes an item of the shopped list onto the flow's nav (in the flow's ordering session). */
async function openProduct(nav: HTMLIonNavElement | null, session: string, orderId: string, itemId: string) {
  if (!nav) return
  await nav.push(() => (
    <OrderSession.Provider value={session}>
      <ProductPage orderId={orderId} itemId={itemId} onDone={() => void nav.pop()} onLeave={() => void nav.popToRoot()} />
    </OrderSession.Provider>
  ))
}

/** Opens a meal's recipe: at a pick's servings, when it's one. */
type OpenMeal = (mealId: string, servings?: number) => void

/** Opens an item of an order's shopped list. */
type OpenProduct = (orderId: string, itemId: string) => void

interface OrderFlowProps {
  /** This order, as it is; otherwise the household's draft, made if there's none. */
  orderId: string | undefined
  startAt: Step | undefined
  /** This opening's ordering session: it takes the order's lock, and makes its changes. */
  session: string
  /** Called with the order shown, once known. */
  onShown: (orderId: string) => void
  onClose: () => void
  onOpenMeal: OpenMeal
  onOpenProduct: OpenProduct
}

/** Opens the household's draft (creating it if needed), or the order given, and shows the step it is at. */
function OrderFlow({ orderId, startAt, session, onShown, onClose, onOpenMeal, onOpenProduct }: OrderFlowProps) {
  const open = useOpenOrder()
  const { mutate: openOrder } = open
  const reopen = () => openOrder({ jobId: crypto.randomUUID(), session })
  const userId = useSession().data?.user.id
  // The household's draft shows at once when it's cached, while opening it confirms it (or makes one).
  const cachedDraftId = useNextOrder().data?.draft?.id
  const order = useOrder(orderId ?? open.data?.id ?? cachedDraftId, session)
  const { refetch } = order
  const [step, setStep] = useState<Step>(startAt ?? 'pick')
  // Where the flow is, as the lock tells the others.
  const shownStep: Step = order.shown === undefined ? 'pick' : stepShown(order.shown, step)
  // The copy cached before opening (the next order's) may be out of date; the lock goes by the order itself.
  const known = !order.isPlaceholderData && !(orderId === undefined && open.isPending) ? order.shown : undefined
  const { lockedOut, takeOver } = useOrderLock(known, session, userId, shownStep)
  const shownId = order.shown?.id
  useEffect(() => {
    if (shownId !== undefined) onShown(shownId)
  }, [shownId, onShown])
  // Opened from "ideas ready" before fresh data arrived: loading its ideas, not
  // the copy from before they were ready.
  const awaited = useAwaitedIdeas()
  const shownOrder = order.shown
  const loading =
    shownOrder !== undefined &&
    loadingIdeas(awaited, {
      id: shownOrder.id,
      hasIdeas: suggestions(shownOrder).length > 0,
      placeholder: order.isPlaceholderData,
      fetchedAt: order.dataUpdatedAt,
    })
  const awaitedOrder = awaited?.orderId
  useEffect(() => {
    if (awaitedOrder !== undefined && shownOrder?.id === awaitedOrder && !loading) stopAwaitingIdeas('shown')
  }, [awaitedOrder, shownOrder, loading])

  // A new day moves the earliest delivery date: fetch the order's limits again.
  const today = localDate(useNow(DAY_CHECK_MS))
  const shownDay = useRef(today)
  useEffect(() => {
    if (shownDay.current === today) return
    shownDay.current = today
    void refetch()
  }, [today, refetch])

  // Opening the flow gets or creates the draft, once (a development re-mount must
  // not repeat it); a given order is only fetched.
  const opened = useRef(false)
  useEffect(() => {
    if (opened.current || orderId !== undefined) return
    opened.current = true
    openOrder({ jobId: crypto.randomUUID(), session })
  }, [openOrder, orderId, session])

  // A discarded draft wins over the copy still on screen, once opening has settled
  // (it may be making a new draft to replace a cached one that's gone).
  const gone = !open.isPending && order.error instanceof ApiError && order.error.status === 404
  if (order.shown && !gone) {
    // The copy on screen stays usable when opening or a refresh fails, with the failure shown above it.
    const notice = open.isError ? (
      <Notice action={<button onClick={reopen}>Try again</button>}>
        Couldn't open the order: {open.error.message}
      </Notice>
    ) : order.isError ? (
      <Notice action={<button onClick={() => void refetch()}>Try again</button>}>
        Couldn't refresh the order: {order.error.message}
      </Notice>
    ) : null
    return (
      <FlowNotice value={notice}>
        {lockedOut ? (
          <LockedStep order={order.shown} lockedOut={lockedOut} onClose={onClose} onTakeOver={takeOver} />
        ) : loading ? (
          <FlowLoading onClose={onClose} label="Loading your meal ideas…" />
        ) : (
          <OrderStep
            order={order.shown}
            step={step}
            setStep={setStep}
            onClose={onClose}
            onOpenMeal={onOpenMeal}
            onOpenProduct={onOpenProduct}
          />
        )}
      </FlowNotice>
    )
  }

  const problem = open.error ?? order.error
  if (!gone && !problem) return <FlowLoading onClose={onClose} />
  return (
    <FlowFrame
      step={null}
      onClose={onClose}
      footer={
        <Button variant="secondary" block onClick={onClose}>
          Close
        </Button>
      }
    >
      {gone ? (
        <div className={styles.problem}>
          <Notice action={<button onClick={onClose}>Close</button>}>
            This draft is gone: someone in your household discarded it.
          </Notice>
        </div>
      ) : (
        <div className={styles.problem}>
          <Notice
            action={
              // A given order is fetched again, never made.
              <button onClick={() => (orderId ? void refetch() : reopen())}>
                Try again
              </button>
            }
          >
            Couldn't open the order: {problem?.message}
          </Notice>
        </div>
      )}
    </FlowFrame>
  )
}

/** The flow while its order loads: the shape of the meals to come, and what it's waiting for, if said. */
function FlowLoading({ onClose, label }: { onClose: () => void; label?: string }) {
  return (
    <FlowFrame
      step={null}
      onClose={onClose}
      footer={
        <Button variant="secondary" block onClick={onClose}>
          Close
        </Button>
      }
    >
      <div className={styles.loading} aria-busy="true">
        {label ? <p className={styles.label}>{label}</p> : <Skeleton height={36} width="60%" radius={10} />}
        <Skeleton height={18} width="90%" radius={8} />
        <div className={styles.grid}>
          <Skeleton height={190} />
          <Skeleton height={190} />
        </div>
      </div>
    </FlowFrame>
  )
}

interface OrderStepProps {
  order: GroceryOrder
  step: Step
  setStep: (step: Step) => void
  onClose: () => void
  onOpenMeal: OpenMeal
  onOpenProduct: OpenProduct
}

/** The screen for where the order is, each step going on to the next. */
function OrderStep({ order, step, setStep, onClose, onOpenMeal, onOpenProduct }: OrderStepProps) {
  const session = useOrderSession()
  const { mutate: startList } = useStartList()
  if (order.status === 'placed') return <PlacedStep order={order} onClose={onClose} />
  if (suggestions(order).length === 0) return <WaitStep order={order} onClose={onClose} />

  const shown = stepShown(order, step)
  const delivery = shownDeliveryDate(order)
  if (shown === 'pick') {
    return <PickStep order={order} onOpenMeal={onOpenMeal} onNext={() => setStep('servings')} onClose={onClose} />
  }
  if (shown === 'servings') {
    return (
      <ServingsStep
        order={order}
        onOpenMeal={onOpenMeal}
        onBack={() => setStep('pick')}
        onNext={() => {
          // The list starts as the member leaves Servings, after the servings are saved.
          startList({ orderId: order.id, jobId: crypto.randomUUID(), session })
          setStep('list')
        }}
        onClose={onClose}
      />
    )
  }
  if (shown === 'list') {
    return (
      <ListStep
        order={order}
        onOpenProduct={(itemId) => onOpenProduct(order.id, itemId)}
        onBack={() => setStep('servings')}
        onNext={() => setStep('delivery')}
        onClose={onClose}
      />
    )
  }
  if (shown === 'delivery' || delivery === null) {
    return <DeliveryStep order={order} onBack={() => setStep('list')} onNext={() => setStep('next')} onClose={onClose} />
  }
  const next = shownNextDeliveryDate(delivery, order.next_delivery_date, order.picks.length)
  if (shown === 'next') {
    return (
      <NextDeliveryStep
        order={order}
        delivery={delivery}
        onBack={() => setStep('delivery')}
        onNext={() => setStep('review')}
        onClose={onClose}
      />
    )
  }
  return (
    <ReviewStep
      order={order}
      delivery={delivery}
      next={next}
      onOpenMeal={onOpenMeal}
      onChange={setStep}
      onBack={() => setStep('next')}
      onClose={onClose}
    />
  )
}
