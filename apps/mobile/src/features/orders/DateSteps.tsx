import { IonIcon } from '@ionic/react'
import { sparkles } from 'ionicons/icons'
import type { ReactNode } from 'react'
import type { GroceryOrder, ISODate } from '../../lib/api/types'
import { formatDate, localDate, toLocalDate } from '../../lib/dates'
import { haptics } from '../../lib/haptics'
import { useNow } from '../../lib/useNow'
import { Button } from '../../ui/Button'
import { DateBar } from '../../ui/DateBar'
import { Notice } from '../../ui/Notice'
import {
  addDays,
  coveredDays,
  dateRange,
  datesForDelivery,
  defaultNextDeliveryDate,
  deliveryBar,
  deliveryGap,
  nextDeliveryBar,
  shownDeliveryDate,
  shownNextDeliveryDate,
} from './dates'
import styles from './DateSteps.module.css'
import { FlowFrame, FooterSummary } from './FlowFrame'
import { plural } from './format'
import { useEditsPending, useSetDates } from './orders'

// How often the steps check the time, so "today" moves on at midnight.
const TODAY_CHECK_MS = 60_000

interface DateStepProps {
  order: GroceryOrder
  onBack: () => void
  onNext: () => void
  onClose: () => void
}

/**
 * Step 2: when this order's groceries arrive. Starts at the target date (the
 * previous order's next delivery), or the earliest date once that has passed.
 */
export function DeliveryStep({ order, onBack, onNext, onClose }: DateStepProps) {
  const setDates = useSetDates()
  const saving = useEditsPending()
  const { number, target_date: target, earliest_delivery_date: earliest } = order
  const shown = shownDeliveryDate(order)
  const today = localDate(useNow(TODAY_CHECK_MS))
  const targetPassed = target !== null && target < earliest
  const previous = number - 1

  function choose(date: ISODate) {
    haptics.tap()
    setDates.mutate({ orderId: order.id, dates: datesForDelivery(date, order.next_delivery_date) })
  }

  // Saves the date shown (it may be the default) before moving on. It is saved
  // even when the order already shows it: that may be a change not yet saved.
  function next() {
    if (shown === null) return
    setDates.mutate(
      { orderId: order.id, dates: datesForDelivery(shown, order.next_delivery_date) },
      { onSuccess: onNext },
    )
  }

  const lede =
    target === null ? (
      <>This is your first order, so there's no target date. When should groceries arrive?</>
    ) : targetPassed ? (
      <>
        The target date, <b>{formatDate(target, 'medium')}</b>, has passed, so Order {number} is set for the
        soonest delivery, <b>{formatDate(earliest, 'medium')}</b>. Change it if you need to.
      </>
    ) : (
      <>
        Order {previous} covers you through <b>{formatDate(addDays(target, -1), 'short')}</b>, so Order {number} is
        set for <b>{formatDate(target, 'medium')}</b>. Change it if you need to.
      </>
    )

  const gap = shown === null ? null : deliveryGap(target, shown)
  const why =
    shown === null ? null : gap === null ? (
      `Your ${plural(order.meal_ids.length, 'meal')} start ${toLocalDate(shown).toLocaleDateString(undefined, { weekday: 'long' })}.`
    ) : gap === 0 ? (
      `Starts the day after Order ${previous}'s last covered day, ${formatDate(addDays(shown, -1), 'short')}.`
    ) : gap > 0 ? (
      // The days from the target date up to this delivery.
      `${plural(gap, 'day')} with no meals covered before it arrives (${dayRange(dateRange(addDays(shown, -gap), addDays(shown, -1)))}).`
    ) : (
      `Arrives while Order ${previous} still covers ${plural(-gap, 'day')}. Groceries may overlap.`
    )

  return (
    <FlowFrame
      step={1}
      onBack={onBack}
      onClose={onClose}
      title="Delivery date"
      lede={lede}
      footer={
        <>
          <FooterSummary
            title={shown ? formatDate(shown, 'medium') : 'No date yet'}
            detail={shown ? 'Delivery date' : 'Pick a delivery date'}
          />
          <Button size="medium" onClick={next} disabled={shown === null || saving}>
            Next
          </Button>
        </>
      }
    >
      <div className={styles.label}>
        Delivery day
        <span>Earliest: {formatDate(earliest, 'medium')}</span>
      </div>
      <DateBar days={deliveryBar(order, today)} selected={shown} onChoose={choose} label="Delivery date" />
      {shown !== null ? (
        <ChoiceCard
          label={shown === target ? 'Target date' : targetPassed && shown === earliest ? 'Soonest delivery' : 'Your pick'}
          recommended={shown === target || (targetPassed && shown === earliest)}
          date={formatDate(shown, 'long')}
        >
          <p className={styles.why}>{why}</p>
        </ChoiceCard>
      ) : (
        <ChoiceCard label="Your pick" recommended={false} date="No date yet">
          <p className={styles.why}>Choose the day your groceries arrive.</p>
        </ChoiceCard>
      )}
      {setDates.isError && <SaveProblem message={setDates.error.message} />}
    </FlowFrame>
  )
}

/**
 * Step 3: when the next order arrives, which sets the days this one covers.
 * Starts at one day per picked meal.
 */
export function NextDeliveryStep({ order, delivery, onBack, onNext, onClose }: DateStepProps & { delivery: ISODate }) {
  const setDates = useSetDates()
  const saving = useEditsPending()
  const picks = order.meal_ids.length
  const recommended = defaultNextDeliveryDate(delivery, picks)
  const shown = shownNextDeliveryDate(delivery, order.next_delivery_date, picks)
  const covered = coveredDays(delivery, shown)
  const days = covered.length

  function choose(date: ISODate) {
    haptics.tap()
    setDates.mutate({ orderId: order.id, dates: { delivery_date: delivery, next_delivery_date: date } })
  }

  // Saves the date shown before moving on (see DeliveryStep).
  function next() {
    setDates.mutate(
      { orderId: order.id, dates: { delivery_date: delivery, next_delivery_date: shown } },
      { onSuccess: onNext },
    )
  }

  // How the meals fit the days: shown the same way whichever day is chosen.
  const fit =
    days === picks
      ? 'One meal a day'
      : days > picks
        ? `${plural(days - picks, 'day')} ${days - picks === 1 ? 'needs' : 'need'} leftovers or takeout`
        : `${plural(picks - days, 'meal')} ${picks - days === 1 ? 'carries' : 'carry'} past the next delivery`

  return (
    <FlowFrame
      step={2}
      onBack={onBack}
      onClose={onClose}
      title="Next delivery"
      lede={
        <>
          This order's meals cover you from <b>{formatDate(delivery, 'medium')}</b> until the next delivery. When
          should Order {order.number + 1} arrive?
        </>
      }
      footer={
        <>
          <FooterSummary title={formatDate(shown, 'medium')} detail={`Next delivery · covers ${plural(days, 'day')}`} />
          <Button size="medium" onClick={next} disabled={saving}>
            Next
          </Button>
        </>
      }
    >
      <div className={styles.label}>Next delivery day</div>
      <DateBar
        days={nextDeliveryBar(order, delivery, picks, shown)}
        selected={shown}
        onChoose={choose}
        label="Next delivery date"
      />
      <ChoiceCard
        label={shown === recommended ? 'Recommended' : 'Your pick'}
        recommended={shown === recommended}
        date={formatDate(shown, 'long')}
      >
        <dl className={styles.facts}>
          <div>
            <dt>Covers</dt>
            <dd>{dayRange(covered)}</dd>
          </div>
          <div>
            <dt>Days</dt>
            <dd>{days}</dd>
          </div>
          <div>
            <dt>Meals</dt>
            <dd>{picks}</dd>
          </div>
        </dl>
        <p className={`${styles.fit} ${days > picks ? styles.short : days < picks ? styles.extra : ''}`}>{fit}</p>
      </ChoiceCard>
      {setDates.isError && <SaveProblem message={setDates.error.message} />}
    </FlowFrame>
  )
}

/** "Mon 28 – Wed 30", or "Mon 28" for one day. */
function dayRange(days: ISODate[]): string {
  const first = days[0]
  const last = days[days.length - 1]
  if (first === undefined || last === undefined) return ''
  return first === last ? formatDate(first, 'short') : `${formatDate(first, 'short')} – ${formatDate(last, 'short')}`
}

interface ChoiceCardProps {
  label: string
  /** The app's suggestion (highlighted) rather than the user's own pick. */
  recommended: boolean
  /** The chosen date, formatted. */
  date: string
  children: ReactNode
}

/**
 * The chosen date, large, with what it means, under the date bar. Its size
 * doesn't depend on the day chosen, so choosing a day moves nothing.
 */
function ChoiceCard({ label, recommended, date, children }: ChoiceCardProps) {
  return (
    <div className={`${styles.choice} ${recommended ? styles.recommended : ''}`}>
      <div className={styles.choiceLabel}>
        {recommended && <IonIcon icon={sparkles} aria-hidden="true" />}
        {label}
      </div>
      <div className={styles.choiceDate}>{date}</div>
      {children}
    </div>
  )
}

function SaveProblem({ message }: { message: string }) {
  return (
    <div className={styles.notice}>
      <Notice>Couldn't save the date: {message}</Notice>
    </div>
  )
}
