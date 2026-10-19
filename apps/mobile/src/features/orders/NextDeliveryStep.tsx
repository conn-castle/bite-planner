import type { GroceryOrder, ISODate } from '../../lib/api/types'
import { addDays, formatDate } from '../../lib/dates'
import { haptics } from '../../lib/haptics'
import { useToday } from '../../lib/useNow'
import { Button } from '../../ui/Button'
import { ChoiceCard } from '../../ui/ChoiceCard'
import { DateBar } from '../../ui/DateBar'
import { FieldLabel } from '../../ui/FieldLabel'
import { Note } from '../../ui/Note'
import { Notice } from '../../ui/Notice'
import { useHousehold } from '../settings/settings'
import { pickedMeals } from './batches'
import { defaultNextDeliveryDate, followingDates, nextDeliveryBar, nextDeliveryWhy, shownNextDeliveryDate } from './dates'
import { FlowFrame, FooterSummary } from './FlowFrame'
import { useOrderSession } from './lock'
import { useEditsPending, useSetDates } from './orders'
import { stepIndex } from './steps'

interface NextDeliveryStepProps {
  order: GroceryOrder
  /** The delivery date shown (the order only gets here with one). */
  delivery: ISODate
  onBack: () => void
  onNext: () => void
  onClose: () => void
}

/** When the next order arrives: one day per meal by default. It sets the next order's decide-by date. */
export function NextDeliveryStep({ order, delivery, onBack, onNext, onClose }: NextDeliveryStepProps) {
  const session = useOrderSession()
  const household = useHousehold().shown
  const setDates = useSetDates()
  const saving = useEditsPending()
  const today = useToday()
  const meals = pickedMeals(order).length
  const next = shownNextDeliveryDate(delivery, order.next_delivery_date, meals)
  const recommended = defaultNextDeliveryDate(delivery, meals)
  const following = order.number + 1

  function choose(date: ISODate) {
    haptics.tap()
    setDates.mutate({ kind: 'dates', orderId: order.id, dates: { delivery_date: delivery, delivery_by: order.delivery_by, next_delivery_date: date }, session })
  }

  function continueToReview() {
    setDates.mutate(
      { kind: 'dates', orderId: order.id, dates: { delivery_date: delivery, delivery_by: order.delivery_by, next_delivery_date: next }, session },
      { onSuccess: onNext },
    )
  }

  return (
    <FlowFrame
      step={stepIndex('next')}
      onBack={onBack}
      onClose={onClose}
      notice={setDates.isError && <Notice>Couldn't save that: {setDates.error.message}</Notice>}
      title="Next delivery"
      lede={`This order's meals cover you from ${formatDate(delivery, 'medium')} until the next delivery. When should Order ${following} arrive?`}
      footer={
        <>
          <FooterSummary
            title={
              household
                ? `Decide by ${formatDate(addDays(next, -household.decide_by_offset_days), 'medium')}`
                : formatDate(next, 'medium')
            }
            detail={`for Order ${following} · delivery ${formatDate(next, 'short')}`}
          />
          <Button large onClick={continueToReview} disabled={saving || setDates.isPending}>
            Next
          </Button>
        </>
      }
    >
      <ChoiceCard
        suggested={next === recommended}
        label={next === recommended ? 'Recommended' : 'Your pick'}
        title={formatDate(next, 'long')}
        detail={nextDeliveryWhy(delivery, next, meals)}
      />
      <FieldLabel>Other days</FieldLabel>
      <DateBar days={nextDeliveryBar(order, delivery, meals, next)} selected={next} onChoose={choose} label="Next delivery date" />
      {household && (
        <Note icon="sliders">
          {followingDates(following, next, household.decide_by_offset_days, household.reminder_lead_days, today)} These
          are household rules. Change them in Settings.
        </Note>
      )}
    </FlowFrame>
  )
}
