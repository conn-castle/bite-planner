import { BATCH_SIZE } from '../../lib/api/limits'
import type { ISODate, NextOrder, PlacedOrder } from '../../lib/api/types'
import { addDays, daysBetween, formatDate, relativeDay } from '../../lib/dates'
import { MealArt } from '../../ui/art'
import { Button } from '../../ui/Button'
import { Chip } from '../../ui/Chip'
import { Countdown } from '../../ui/Countdown'
import { Icon } from '../../ui/Icon'
import { OrderCard, OrderCardActions, OrderCardBody, OrderCardLine } from '../../ui/OrderCard'
import { YourReminder } from '../notifications/YourReminder'
import { lastCovered, onHand, urgency } from './home'
import styles from './NextCard.module.css'

interface NextCardProps {
  /** The next order; no one has started its draft. */
  next: NextOrder
  placed: PlacedOrder[]
  today: ISODate
  /** The next step on Home. */
  top: boolean
  onStart: () => void
}

/** The next order before anyone starts it: when to decide by, and a way to start. */
export function NextCard({ next, placed, today, top, onStart }: NextCardProps) {
  const decideBy = next.decide_by_date
  const target = next.target_date
  if (decideBy === null || target === null) return <WelcomeCard onStart={onStart} />

  const now = urgency(next, today)
  const days = daysBetween(today, decideBy)
  const last = lastCovered(placed)
  // Today's meals, and the last day they cover.
  const hand = onHand(placed, today)
  const handLine = hand ? `Your ${hand.meals} on hand last through ${through(hand.through, today)}. ` : ''
  const countdown = <Countdown days={days} tone={now === 'soon' || now === 'remind' ? 'accent' : 'quiet'} />
  // Reminders: to everyone once they've started; when they start, before.
  const everyoneLine = (
    <OrderCardLine icon="bell">
      Reminders go to everyone daily until it's placed · <YourReminder bold />
    </OrderCardLine>
  )
  const start = next.reminders_start_date
  const startLine = start && (
    <OrderCardLine icon="bell">
      Reminders start <b>{formatDate(start, 'short')}</b>, daily · <YourReminder />
    </OrderCardLine>
  )
  const decideLine = (
    <OrderCardLine icon="cal">
      Decide by <b>{formatDate(decideBy, 'short')}</b> · delivery {formatDate(target, 'short')}
    </OrderCardLine>
  )

  if (now === 'late') {
    const soonest = next.earliest_delivery_date
    return (
      <OrderCard
        number={next.number}
        look="late"
        chip={
          <Chip tone="danger" icon="alert">
            Overdue
          </Chip>
        }
        title={`Pick meals for ${relativeDay(soonest, today, 'long')}'s delivery`}
        subtitle={`${formatDate(decideBy, 'weekday')} was the last day to pick meals for ${formatDate(target, 'weekday')}'s delivery. The soonest delivery now is ${relativeDay(soonest, today, 'long')}, ${formatDate(soonest, 'monthDay')}.`}
        aside={countdown}
      >
        <OrderCardBody>
          {last !== null && last < today && (
            <OrderCardLine icon="alert" tone="danger">
              No meals on hand since {formatDate(addDays(last, 1), 'short')}
            </OrderCardLine>
          )}
          {everyoneLine}
          <OrderCardActions>
            <Button variant="danger" block onClick={onStart}>
              Pick meals now
            </Button>
          </OrderCardActions>
        </OrderCardBody>
      </OrderCard>
    )
  }

  if (now === 'soon') {
    return (
      <OrderCard
        number={next.number}
        look="soon"
        chip={
          <Chip tone="accent" icon="bell">
            Due {relativeDay(decideBy, today, 'short')}
          </Chip>
        }
        title={`Pick meals for ${formatDate(target, 'weekday')}'s delivery`}
        subtitle={`${handLine}Pick meals ${days === 0 ? 'today' : 'by tomorrow'} so groceries arrive ${relativeDay(target, today, 'long')}.`}
        aside={countdown}
      >
        <OrderCardBody>
          {everyoneLine}
          <OrderCardActions>
            <Button variant="accent" block onClick={onStart}>
              Pick meals
            </Button>
          </OrderCardActions>
        </OrderCardBody>
      </OrderCard>
    )
  }

  if (now === 'remind') {
    return (
      <OrderCard
        number={next.number}
        look="remind"
        chip={
          <Chip tone="accentText" icon="bell">
            Reminders on
          </Chip>
        }
        title={`Pick meals for ${formatDate(target, 'weekday')}'s delivery`}
        subtitle={`${handLine}Decide by ${formatDate(decideBy, 'weekday')}, ${formatDate(decideBy, 'monthDay')}, and groceries arrive ${formatDate(target, 'weekday')}.`}
        aside={countdown}
      >
        <OrderCardBody>
          {everyoneLine}
          <OrderCardActions>
            <Button variant="accent" block onClick={onStart}>
              Pick meals
            </Button>
          </OrderCardActions>
        </OrderCardBody>
      </OrderCard>
    )
  }

  if (top) {
    return (
      <OrderCard
        number={next.number}
        look="draft"
        chip={<Chip>Draft</Chip>}
        title="You're all caught up"
        subtitle={`${hand ? `${capitalize(hand.meals)} on hand through ${through(hand.through, today)}. ` : ''}${
          start
            ? `Reminders to pick ${formatDate(target, 'weekday')}'s meals start ${relativeDay(start, today, 'long')}.`
            : `Pick ${formatDate(target, 'weekday')}'s meals by ${formatDate(decideBy, 'weekday')}.`
        }`}
        aside={countdown}
      >
        <OrderCardBody>
          {decideLine}
          {startLine}
          <OrderCardActions>
            <Button variant="white" block onClick={onStart}>
              Start early
            </Button>
          </OrderCardActions>
        </OrderCardBody>
      </OrderCard>
    )
  }

  return (
    <OrderCard
      number={next.number}
      look="draft"
      chip={<Chip>Draft</Chip>}
      title={`Decide in ${days} days`}
      subtitle={`Pick meals by ${formatDate(decideBy, 'weekday')} for delivery ${formatDate(target, 'medium')}.`}
      aside={countdown}
    >
      <OrderCardBody>
        {startLine}
        <OrderCardActions>
          <Button variant="secondary" onClick={onStart}>
            Start early <Icon name="chevR" />
          </Button>
        </OrderCardActions>
      </OrderCardBody>
    </OrderCard>
  )
}

/** Home before the household's first order (.welcome), with the cards to come sketched under it. */
function WelcomeCard({ onStart }: { onStart: () => void }) {
  return (
    // One item of the card stack, so the sketched cards stay under it on a wide screen.
    <div className={styles.welcomeGroup}>
      <article className={styles.welcome}>
        <div className={styles.art} aria-hidden="true">
          {[0, 1, 2].map((index) => (
            <span key={index} className={styles.meal}>
              <MealArt />
            </span>
          ))}
        </div>
        <h2 className={styles.title}>Let's plan your first meals</h2>
        <p className={styles.subtitle}>
          Chef suggests {BATCH_SIZE} easy meals. Add the ones you want, choose a delivery date, and the order shows up here as a
          card you can track.
        </p>
        <Button variant="accent" block className={styles.start} onClick={onStart}>
          Plan my first meals
        </Button>
      </article>
      <div className={styles.ghost} aria-hidden="true" />
      <div className={`${styles.ghost} ${styles.faint}`} aria-hidden="true" />
    </div>
  )
}

/** The last day meals last: "tonight" when that's today. */
function through(last: ISODate, today: ISODate): string {
  return last === today ? 'tonight' : relativeDay(last, today, 'long')
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}
