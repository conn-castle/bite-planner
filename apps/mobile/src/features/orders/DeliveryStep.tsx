import type { ClockTime, GroceryOrder, ISODate } from '../../lib/api/types'
import { addDays, formatClockTime, formatDate, formatWindow, relativeDay } from '../../lib/dates'
import { haptics } from '../../lib/haptics'
import { useToday } from '../../lib/useNow'
import { Button } from '../../ui/Button'
import { ChoiceCard } from '../../ui/ChoiceCard'
import { DateBar } from '../../ui/DateBar'
import { FieldLabel } from '../../ui/FieldLabel'
import { Group, Row } from '../../ui/Group'
import { Notice } from '../../ui/Notice'
import { TimeField } from '../../ui/TimeField'
import { useHousehold } from '../settings/settings'
import { pickedMeals } from './batches'
import { datesForDelivery, deliveryBar, deliveryChoice, deliveryWhy, shownDeliveryBy, shownDeliveryDate } from './dates'
import { FlowFrame, FooterSummary } from './FlowFrame'
import { useOrderSession } from './lock'
import { useEditsPending, useSetDates } from './orders'
import styles from './Steps.module.css'
import { stepIndex } from './steps'

const CHOICE_LABEL = { target: 'Target date', soonest: 'Soonest delivery', pick: 'Your pick' } as const

interface DeliveryStepProps {
  order: GroceryOrder
  onBack: () => void
  onNext: () => void
  onClose: () => void
}

/**
 * When the order arrives: the target date (the previous order's next delivery),
 * or another day; and the latest time it should arrive by that day (the
 * household's delivery time to start with).
 */
export function DeliveryStep({ order, onBack, onNext, onClose }: DeliveryStepProps) {
  const session = useOrderSession()
  const setDates = useSetDates()
  const saving = useEditsPending()
  const today = useToday()
  const delivery = shownDeliveryDate(order)
  const choice = delivery === null ? null : deliveryChoice(order, delivery)
  const earliest = order.earliest_delivery_date
  const household = useHousehold()
  const by = shownDeliveryBy(order, household.shown?.delivery_time)
  // No window ends by the time or includes it: an order that couldn't be booked.
  const unbookable = order.best_window === null

  function choose(date: ISODate) {
    haptics.tap()
    setDates.mutate({
      kind: 'dates',
      orderId: order.id,
      dates: datesForDelivery(date, order.delivery_by, order.next_delivery_date),
      session,
    })
  }

  function chooseTime(time: ClockTime) {
    const dates =
      delivery === null
        ? { delivery_date: null, delivery_by: time, next_delivery_date: null }
        : datesForDelivery(delivery, time, order.next_delivery_date)
    setDates.mutate({ kind: 'dates', orderId: order.id, dates, session })
  }

  return (
    <FlowFrame
      step={stepIndex('delivery')}
      onBack={onBack}
      onClose={onClose}
      notice={
        setDates.isError ? (
          <Notice>Couldn't save that: {setDates.error.message}</Notice>
        ) : by === null && household.isError ? (
          <Notice>Couldn't load the household's delivery time: {household.error.message}</Notice>
        ) : unbookable && by ? (
          <Notice>No delivery window ends by {formatClockTime(by)} or includes it. Choose a later time.</Notice>
        ) : null
      }
      title="Delivery"
      lede={<Lede order={order} />}
      footer={
        <>
          <FooterSummary
            title={delivery ? formatDate(delivery, 'medium') : 'No date yet'}
            detail={delivery ? (by ? `Arrives by ${formatClockTime(by)}` : 'Delivery date') : 'Pick a delivery date'}
          />
          <Button large onClick={onNext} disabled={delivery === null || by === null || unbookable || saving}>
            Next
          </Button>
        </>
      }
    >
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
      {by && (
        <div className={styles.groups}>
          <Group footer={windowNote(order, by)}>
            <Row
              label="Arrive by"
              detail={<TimeField value={by} label="Arrive by" onChange={chooseTime} />}
            />
          </Group>
        </div>
      )}
    </FlowFrame>
  )
}

/** Which window booking picks for the time: "Booked in the latest delivery window ending by then…". */
function windowNote({ best_window: best, fallback_window: fallback }: GroceryOrder, by: ClockTime): string {
  if (best === null) return 'No delivery window ends by then. Choose a later time.'
  if (best.end > by) return `Booked ${formatWindow(best)}, the one window that includes it.`
  return fallback
    ? `Booked in the latest delivery window ending by then (${formatWindow(best)} if it's open), or ${formatWindow(fallback)} if nothing earlier is left.`
    : `Booked in the latest delivery window ending by then (${formatWindow(best)} if it's open).`
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

