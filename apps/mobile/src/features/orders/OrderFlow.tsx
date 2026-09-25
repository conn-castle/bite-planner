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
import { suggestions } from './batches'
import { DeliveryStep, NextDeliveryStep } from './DateSteps'
import { FlowFrame } from './FlowFrame'
import { FlowNotice } from './flowNotice'
import styles from './OrderFlow.module.css'
import { draftQuery, useOpenOrder, useOrder } from './orders'
import { PickStep } from './PickStep'
import { PlacedStep, ReviewStep } from './ReviewStep'
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

  // Stable, or the nav would start over: it resets whenever its root changes.
  const root = useCallback(
    () => <OrderFlow onClose={() => void modal.current?.dismiss()} onOpenMeal={(mealId) => void openMeal(nav.current, mealId)} />,
    [],
  )

  function dismissed() {
    // The flow kept its own copy of the order current; the draft may be out of date.
    void queryClient.invalidateQueries({ queryKey: draftQuery.queryKey })
    onDismiss()
  }

  return (
    <IonModal ref={modal} isOpen={isOpen} onDidDismiss={dismissed} presentingElement={presentingElement}>
      <IonNav ref={nav} root={root} />
    </IonModal>
  )
}

/** Pushes a recipe onto the flow's nav. */
async function openMeal(nav: HTMLIonNavElement | null, mealId: string) {
  if (!nav) return
  await nav.push(() => <MealPage mealId={mealId} />)
  // A recipe once opened as a blank card (its page left invisible); report it if that happens again.
  const page = (await nav.getActive())?.element
  if (page && getComputedStyle(page).opacity === '0') {
    logger.error('orders.recipe_not_shown', { mealId, pageClasses: page.className })
  }
}

type Step = 'pick' | 'delivery' | 'next' | 'review'

interface OrderFlowProps {
  onClose: () => void
  onOpenMeal: (mealId: string) => void
}

/** Opens the household's draft (creating it if needed) and shows the step it is at. */
function OrderFlow({ onClose, onOpenMeal }: OrderFlowProps) {
  const open = useOpenOrder()
  const { mutate: openOrder } = open
  const order = useOrder(open.data?.id)
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

  // A discarded draft wins over the copy still on screen.
  const gone = order.error instanceof ApiError && order.error.status === 404
  if (order.shown && !gone) {
    // The copy on screen stays usable when a refresh fails, with the failure shown above it.
    const notice = order.isError ? (
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
  onOpenMeal: (mealId: string) => void
}

/**
 * The screen for where the order is. The step the user chose is shown unless
 * the order can't support it (e.g. another member removed every pick or
 * changed a date), in which case the earliest step that needs attention is.
 */
function OrderStep({ order, step, setStep, onClose, onOpenMeal }: OrderStepProps) {
  if (order.status === 'placed') return <PlacedStep order={order} onClose={onClose} />
  if (suggestions(order).length === 0) return <WaitStep order={order} onClose={onClose} />

  const { delivery_date: delivery, next_delivery_date: next } = order
  const hasPicks = order.meal_ids.length > 0
  const deliveryValid = delivery !== null && delivery >= order.earliest_delivery_date

  if (step === 'pick' || !hasPicks) {
    return <PickStep order={order} onOpenMeal={onOpenMeal} onNext={() => setStep('delivery')} onClose={onClose} />
  }
  if (step === 'delivery' || !deliveryValid) {
    return (
      <DeliveryStep order={order} onBack={() => setStep('pick')} onNext={() => setStep('next')} onClose={onClose} />
    )
  }
  if (step === 'next' || next === null || next <= delivery) {
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
      onBack={() => setStep('next')}
      onClose={onClose}
    />
  )
}
