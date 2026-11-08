import { useEffect, useRef } from 'react'
import type { ClockTime, GroceryOrder, ISODate } from '../../lib/api/types'
import { formatClockTime, formatDate, formatWindow } from '../../lib/dates'
import { haptics } from '../../lib/haptics'
import { useNow } from '../../lib/useNow'
import { Button } from '../../ui/Button'
import { ChoiceCard } from '../../ui/ChoiceCard'
import { CheckoutWait } from '../../ui/CheckoutWait'
import { DateBar } from '../../ui/DateBar'
import { FieldLabel } from '../../ui/FieldLabel'
import { Group, Row } from '../../ui/Group'
import { Notice } from '../../ui/Notice'
import { TimeField } from '../../ui/TimeField'
import { useHousehold } from '../settings/settings'
import { pickedMeals } from './batches'
import { availableWindows, availabilityFresh, datesForDelivery, deliveryAvailable, deliveryBar, deliveryExpired, deliveryWhy, shownDeliveryBy, shownDeliveryDate, storeDateTime } from './dates'
import { FlowFrame, FooterSummary } from './FlowFrame'
import { useOrderSession } from './lock'
import { useEditsPending, useQuoteOrder, useSetDates } from './orders'
import styles from './Steps.module.css'
import { stepIndex } from './steps'
import { quoteState } from './quote'
import { storeName } from './list'

interface DeliveryStepProps {
  order: GroceryOrder
  onBack: () => void
  onNext: () => void
  onClose: () => void
}

/** Choose from store-observed windows; a saved selection is never advanced automatically. */
export function DeliveryStep({ order, onBack, onNext, onClose }: DeliveryStepProps) {
  const session = useOrderSession()
  const setDates = useSetDates()
  const quote = useQuoteOrder()
  const { mutate: readQuote } = quote
  const saving = useEditsPending()
  const now = useNow(1000)
  const today: ISODate = storeDateTime(now, order.delivery_time_zone).slice(0, 10)
  const delivery: ISODate | null = shownDeliveryDate(order)
  const windows = delivery ? availableWindows(order, delivery, now) : []
  const household = useHousehold()
  const by: ClockTime | null = order.delivery_by ?? (windows === null ? shownDeliveryBy(order, household.shown?.delivery_time) : null)
  const asked = useRef(false)
  const fresh: boolean = availabilityFresh(order, now)
  const state = quoteState(order.quote)
  const reading: boolean = quote.isPending || order.quote?.job.status === 'pending' || order.quote?.job.status === 'running'
  useEffect(() => {
    if (reading) return
    if (fresh) { asked.current = false; return }
    if (asked.current || state === 'blocked') return
    asked.current = true
    readQuote({ orderId: order.id, jobId: crypto.randomUUID(), session })
  }, [fresh, reading, state, readQuote, order.id, session])

  function checkAgain() {
    asked.current = true
    readQuote({ orderId: order.id, jobId: crypto.randomUUID(), session, refresh: true })
  }

  const expired: boolean = deliveryExpired(delivery, by, order.delivery_time_zone, now)
  const available: boolean = delivery !== null && by !== null && deliveryAvailable(order, delivery, by, now)
  const days = deliveryBar({ ...order, earliest_delivery_date: today }, today).map((day) => {
    const offered = availableWindows(order, day.date, now)
    return { ...day, selectable: day.selectable && (offered === null || offered.length > 0) }
  })

  function choose(date: ISODate) {
    haptics.tap()
    const keptBy: ClockTime | null = order.delivery_by !== null && deliveryAvailable(order, date, order.delivery_by, now) ? order.delivery_by : null
    setDates.mutate({ kind: 'dates', orderId: order.id, dates: datesForDelivery(date, keptBy, order.next_delivery_date), session })
  }

  function chooseTime(time: ClockTime) {
    setDates.mutate({
      kind: 'dates', orderId: order.id,
      dates: delivery === null ? { delivery_date: null, delivery_by: time, next_delivery_date: null } : datesForDelivery(delivery, time, order.next_delivery_date),
      session,
    })
  }

  function continueToNext() {
    if (delivery === null || by === null) return
    setDates.mutate({
      kind: 'dates', orderId: order.id,
      dates: datesForDelivery(delivery, by, order.next_delivery_date), session,
    }, { onSuccess: onNext })
  }

  const problem: string | null = setDates.error?.message ?? quote.error?.message ??
    (order.quote?.job.status === 'failed' ? order.quote.job.error : null) ?? order.quote?.blocked_because ??
    (expired ? 'Your saved delivery date and time have passed. Choose a new delivery; nothing has been moved.' : null)

  if (reading || quote.isError || (!fresh && state !== 'blocked')) {
    return <CheckoutWait
      order={order} step="delivery" store={storeName(order)}
      failed={state === 'failed'} signedOut={order.quote?.signed_out ?? false}
      startFailed={quote.error?.message ?? (!reading && order.quote?.current === false
        ? 'Your order changed during the check. Try again to check its delivery times and total.' : null)}
      retrying={quote.isPending}
      onRetry={checkAgain} onBack={onBack} onClose={onClose}
    />
  }

  return (
    <FlowFrame
      step={stepIndex('delivery')} onBack={onBack} onClose={onClose} title="Delivery"
      lede="Choose when your groceries arrive. Times are local to the delivery address. A different day or later arrival always needs your approval."
      notice={problem ? <Notice>{problem}</Notice> : null}
      footer={<>
        <FooterSummary title={delivery ? formatDate(delivery, 'medium') : 'Choose a delivery date'} detail={by ? `Arrives by ${formatClockTime(by)}` : 'Choose an arrival time'} />
        <Button large onClick={continueToNext} disabled={!available || saving || setDates.isPending}>Next</Button>
      </>}
    >
      {delivery && <ChoiceCard suggested={order.delivery_date === null} label={order.delivery_date ? 'Your selection' : 'Suggested date'} title={formatDate(delivery, 'long')} detail={deliveryWhy(order, delivery, pickedMeals(order).length)} />}
      <FieldLabel>Delivery day</FieldLabel>
      <DateBar days={days} selected={delivery} onChoose={choose} label="Delivery date" />
      <div className={styles.groups}>
        <Group title="Delivery times" footer="Availability can change before booking. Nothing later is ordered without your approval.">
          {delivery && windows === null && <>
            <Row label="This day isn't offered yet" sub="You can plan ahead. Availability will be checked when it books." />
            {by && <Row label="Arrive by" detail={<TimeField value={by} label="Arrive by" onChange={chooseTime} />} />}
          </>}
          {windows !== null && windows.map((window) => <Row
            key={`${window.start}-${window.end}`} label={formatWindow(window)} detail={by === window.end ? 'Selected' : undefined}
            sub={`Arrive by ${formatClockTime(window.end)}`} onClick={() => chooseTime(window.end)} disabled={saving || reading}
          />)}
          {!reading && windows !== null && windows.length === 0 && <Row label={fresh ? 'No times left for this day' : 'Check current delivery times'} />}
          {order.quote?.availability && <Row label="Times checked" detail={new Date(order.quote.availability.observed_at).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })} />}
          <Row label="Check again" chevron onClick={checkAgain} disabled={reading || saving} />
        </Group>
      </div>
    </FlowFrame>
  )
}
