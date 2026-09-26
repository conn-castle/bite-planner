import { IonModal, IonNav } from '@ionic/react'
import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../../lib/api/errors'
import { localDate } from '../../lib/dates'
import { logger } from '../../lib/log'
import type { GroceryOrder } from '../../lib/api/types'
import { useNow } from '../../lib/useNow'
import { Button } from '../../ui/Button'
import { Notice } from '../../ui/Notice'
import { Skeleton } from '../../ui/Skeleton'
import { MealPage } from '../meals/MealPage'
import { setOrderFlowOpen } from '../notifications/notifications'
import { suggestions } from './batches'
import { DatesStep } from './DatesStep'
import { FlowFrame } from './FlowFrame'
import { FlowNotice } from './flowNotice'
import styles from './OrderFlow.module.css'
import { nextQuery, useNextOrder, useOpenOrder, useOrder } from './orders'
import { PickStep } from './PickStep'
import { PlacedStep } from './PlacedStep'
import { WaitStep } from './WaitStep'

// How often the flow checks whether the phone's calendar day has changed.
const DAY_CHECK_MS = 60_000

interface OrderFlowModalProps {
  isOpen: boolean
  onDismiss: () => void
  /** The page the card modal slides over. */
  presentingElement: HTMLElement | undefined
}

/**
 * The create-order flow, as a card over the current page. Closing it (Close,
 * or a swipe down) keeps the household's draft to resume later. A recipe opens
 * inside the card, on its own navigation stack.
 */
export function OrderFlowModal({ isOpen, onDismiss, presentingElement }: OrderFlowModalProps) {
  const queryClient = useQueryClient()
  const modal = useRef<HTMLIonModalElement>(null)
  const nav = useRef<HTMLIonNavElement>(null)

  // While it's open, "ideas ready" needs no toast: the ideas show here.
  useEffect(() => {
    setOrderFlowOpen(isOpen)
    return () => setOrderFlowOpen(false)
  }, [isOpen])

  // Stable, or the nav would start over: it resets whenever its root changes.
  const root = useCallback(
    () => (
      <OrderFlow
        onClose={() => void modal.current?.dismiss()}
        onOpenMeal={(mealId, servings) => void openMeal(nav.current, mealId, servings)}
      />
    ),
    [],
  )

  function dismissed() {
    // The flow kept its own copy of the order current; the next order's may be out of date.
    void queryClient.invalidateQueries({ queryKey: nextQuery.queryKey })
    onDismiss()
  }

  return (
    <IonModal ref={modal} isOpen={isOpen} onDidDismiss={dismissed} presentingElement={presentingElement}>
      <IonNav ref={nav} root={root} />
    </IonModal>
  )
}

/** Pushes a recipe onto the flow's nav: at a pick's servings, when it's one. */
async function openMeal(nav: HTMLIonNavElement | null, mealId: string, servings: number | undefined) {
  if (!nav) return
  await nav.push(() => <MealPage mealId={mealId} servings={servings} />)
  // A recipe once opened as a blank card (its page left invisible); report it if that happens again.
  const page = (await nav.getActive())?.element
  if (page && getComputedStyle(page).opacity === '0') {
    logger.error('orders.recipe_not_shown', { mealId, pageClasses: page.className })
  }
}

type Step = 'pick' | 'dates'

/** Opens a meal's recipe: at a pick's servings, when it's one. */
type OpenMeal = (mealId: string, servings?: number) => void

interface OrderFlowProps {
  onClose: () => void
  onOpenMeal: OpenMeal
}

/** Opens the household's draft (creating it if needed) and shows the step it is at. */
function OrderFlow({ onClose, onOpenMeal }: OrderFlowProps) {
  const open = useOpenOrder()
  const { mutate: openOrder } = open
  // The household's draft shows at once when it's cached, while opening it confirms it (or makes one).
  const cachedDraftId = useNextOrder().data?.draft?.id
  const order = useOrder(open.data?.id ?? cachedDraftId)
  const { refetch } = order
  const [step, setStep] = useState<Step>('pick')

  // A new day moves the earliest delivery date: fetch the order's limits again.
  const today = localDate(useNow(DAY_CHECK_MS))
  const shownDay = useRef(today)
  useEffect(() => {
    if (shownDay.current === today) return
    shownDay.current = today
    void refetch()
  }, [today, refetch])

  // Opening the flow gets or creates the draft, once (a development re-mount must not repeat it).
  const opened = useRef(false)
  useEffect(() => {
    if (opened.current) return
    opened.current = true
    openOrder({ jobId: crypto.randomUUID() })
  }, [openOrder])

  // A discarded draft wins over the copy still on screen, once opening has settled
  // (it may be making a new draft to replace a cached one that's gone).
  const gone = !open.isPending && order.error instanceof ApiError && order.error.status === 404
  if (order.shown && !gone) {
    // The copy on screen stays usable when opening or a refresh fails, with the failure shown above it.
    const notice = open.isError ? (
      <Notice action={<button onClick={() => openOrder({ jobId: crypto.randomUUID() })}>Try again</button>}>
        Couldn't open the order: {open.error.message}
      </Notice>
    ) : order.isError ? (
      <Notice action={<button onClick={() => void refetch()}>Try again</button>}>
        Couldn't refresh the order: {order.error.message}
      </Notice>
    ) : null
    return (
      <FlowNotice value={notice}>
        <OrderStep order={order.shown} step={step} setStep={setStep} onClose={onClose} onOpenMeal={onOpenMeal} />
      </FlowNotice>
    )
  }

  const problem = open.error ?? order.error
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
      ) : problem ? (
        <div className={styles.problem}>
          <Notice action={<button onClick={() => openOrder({ jobId: crypto.randomUUID() })}>Try again</button>}>
            Couldn't open the order: {problem.message}
          </Notice>
        </div>
      ) : (
        <div className={styles.loading} aria-busy="true">
          <Skeleton height={36} width="60%" radius={10} />
          <Skeleton height={18} width="90%" radius={8} />
          <div className={styles.grid}>
            <Skeleton height={190} />
            <Skeleton height={190} />
          </div>
        </div>
      )}
    </FlowFrame>
  )
}

interface OrderStepProps {
  order: GroceryOrder
  step: Step
  setStep: (step: Step) => void
  onClose: () => void
  onOpenMeal: OpenMeal
}

/**
 * The screen for where the order is. The step the user chose is shown unless
 * the order can't support it (e.g. another member removed every pick), in which
 * case picking meals is.
 */
function OrderStep({ order, step, setStep, onClose, onOpenMeal }: OrderStepProps) {
  if (order.status === 'placed') return <PlacedStep order={order} onClose={onClose} />
  if (suggestions(order).length === 0) return <WaitStep order={order} onClose={onClose} />

  if (step === 'pick' || order.picks.length === 0) {
    return <PickStep order={order} onOpenMeal={onOpenMeal} onNext={() => setStep('dates')} onClose={onClose} />
  }
  return <DatesStep order={order} onOpenMeal={onOpenMeal} onBack={() => setStep('pick')} onClose={onClose} />
}
