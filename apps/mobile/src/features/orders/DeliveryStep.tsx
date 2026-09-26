import type { GroceryOrder, ISODate } from '../../lib/api/types'
import { addDays, formatDate, localDate, relativeDay } from '../../lib/dates'
import { haptics } from '../../lib/haptics'
import { useNow } from '../../lib/useNow'
import { Button } from '../../ui/Button'
import { ChoiceCard } from '../../ui/ChoiceCard'
import { DateBar } from '../../ui/DateBar'
import { FieldLabel } from '../../ui/FieldLabel'
import { Notice } from '../../ui/Notice'
import { pickedMeals } from './batches'
import { datesForDelivery, deliveryBar, deliveryChoice, deliveryWhy, shownDeliveryDate } from './dates'
import { FlowFrame, FooterSummary } from './FlowFrame'
import { useOrderSession } from './lock'
import { useEditsPending, useSetDates } from './orders'
import styles from './Steps.module.css'
import { stepIndex } from './steps'

// How often the step checks the time, so "today" moves on at midnight.
const TODAY_CHECK_MS = 60_000

const CHOICE_LABEL = { target: 'Target date', soonest: 'Soonest delivery', pick: 'Your pick' } as const

interface DeliveryStepProps {
  order: GroceryOrder
  onBack: () => void
  onNext: () => void
  onClose: () => void
}

/** When the order arrives: the target date (the previous order's next delivery), or another day. */
export function DeliveryStep({ order, onBack, onNext, onClose }: DeliveryStepProps) {
  const session = useOrderSession()
  const setDates = useSetDates()
  const saving = useEditsPending()
  const today = localDate(useNow(TODAY_CHECK_MS))
  const delivery = shownDeliveryDate(order)
  const choice = delivery === null ? null : deliveryChoice(order, delivery)
  const earliest = order.earliest_delivery_date

  function choose(date: ISODate) {
    haptics.tap()
    setDates.mutate({ orderId: order.id, dates: datesForDelivery(date, order.next_delivery_date), session })
  }

  return (
    <FlowFrame
      step={stepIndex('delivery')}
      onBack={onBack}
      onClose={onClose}
      title="Delivery date"
      lede={<Lede order={order} />}
      footer={
        <>
          <FooterSummary
            title={delivery ? formatDate(delivery, 'medium') : 'No date yet'}
            detail={delivery ? 'Delivery date' : 'Pick a delivery date'}
          />
          <Button large onClick={onNext} disabled={delivery === null || saving}>
            Next
          </Button>
        </>
      }
    >
      {setDates.isError && (
        <div className={styles.notice}>
          <Notice>Couldn't save that: {setDates.error.message}</Notice>
        </div>
      )}
      {delivery && choice && (
        <ChoiceCard
          suggested={choice !== 'pick'}
          label={CHOICE_LABEL[choice]}
          title={formatDate(delivery, 'long')}
          detail={deliveryWhy(order, delivery, pickedMeals(order).length)}
        />
      )}
      <FieldLabel aside={`Earliest: ${relativeDay(earliest, today, 'long')}, ${formatDate(earliest, 'monthDay')}`}>
        {delivery ? 'Other days' : 'Delivery day'}
      </FieldLabel>
      <DateBar days={deliveryBar(order, today)} selected={delivery} onChoose={choose} label="Delivery date" />
    </FlowFrame>
  )
}

/** Where the default comes from: the first order has none; a passed target moves to the soonest delivery. */
function Lede({ order }: { order: GroceryOrder }) {
  const { number, target_date: target, earliest_delivery_date: earliest } = order
  if (target === null) return <>This is your first order, so there's no target date. When should groceries arrive?</>
  if (target < earliest) {
    return (
      <>
        The target date, <b>{formatDate(target, 'medium')}</b>, has passed, so Order {number} is set for the soonest
        delivery, <b>{formatDate(earliest, 'medium')}</b>. Change it if you need to.
      </>
    )
  }
  return (
    <>
      Order {number - 1} covers you through <b>{formatDate(addDays(target, -1), 'short')}</b>, so Order {number} is
      set for <b>{formatDate(target, 'medium')}</b>. Change it if you need to.
    </>
  )
}

