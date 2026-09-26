import type { ReactNode } from 'react'
import type { ISODate, NextOrder, PlacedOrder } from '../../lib/api/types'
import { addDays, daysBetween, formatDate, relativeDay } from '../../lib/dates'
import { Button } from '../../ui/Button'
import { Chip } from '../../ui/Chip'
import { Icon } from '../../ui/Icon'
import { MealStack } from '../../ui/MealStack'
import { OrderCard, OrderCardBody, OrderCardDivider, OrderCardNote } from '../../ui/OrderCard'
import { OrderTrack, type TrackDate } from '../../ui/OrderTrack'
import { MealRow } from '../meals/MealRow'
import { plural } from '../orders/format'
import { byWhom, coverage, followingOrder, phase, placedOn, placedTitle } from './home'
import styles from './PlacedCard.module.css'

interface PlacedCardProps {
  order: PlacedOrder
  /** The next step on Home: says more, and starts with its meals open. */
  top: boolean
  /** The household's placed orders on Home, and its next order, for what comes after this one. */
  placed: PlacedOrder[]
  next: NextOrder
  today: ISODate
  userId: string | undefined
  mealsOpen: boolean
  onToggleMeals: () => void
  onOpen: () => void
  onOpenMeal: (mealId: string) => void
}

/** A placed order on Home: when its groceries come, how far along it is, and its meals. */
export function PlacedCard({
  order,
  top,
  placed,
  next,
  today,
  userId,
  mealsOpen,
  onToggleMeals,
  onOpen,
  onOpenMeal,
}: PlacedCardProps) {
  const now = phase(order, today)
  const meals = plural(order.meals.length, 'meal')
  const by = byWhom(order.placed_by, userId)
  const viewOrder = (
    <Button block className={styles.view} onClick={onOpen}>
      View order
    </Button>
  )

  let body: ReactNode = null
  if (now === 'waiting') {
    const following = followingOrder(order, placed, next)
    const decide = following?.kind === 'next' && following.next.decide_by_date
    body = top ? (
      <>
        <p className={styles.act}>
          <span className={styles.overline}>
            Placed {relativeDay(placedOn(order), today, 'short')} by {by}
          </span>
          <span className={styles.lead}>
            Groceries for {meals} arrive {relativeDay(order.delivery_date, today, 'long')},{' '}
            {formatDate(order.delivery_date, 'monthDay')}.
          </span>
          {decide && <span className={styles.more}>Next, pick meals by {formatDate(decide, 'medium')}.</span>}
        </p>
        {viewOrder}
      </>
    ) : (
      <OrderCardNote icon="truck">
        Placed {relativeDay(placedOn(order), today, 'short')} by {by}. Groceries arrive{' '}
        {formatDate(order.delivery_date, 'medium')}.
      </OrderCardNote>
    )
  } else if (now === 'arriving') {
    body = top ? (
      <>
        <p className={styles.act}>
          <span className={styles.lead}>Everything for {meals} arrives today.</span>
          <span className={styles.more}>
            They cover today through {formatDate(addDays(order.next_delivery_date, -1), 'weekday')}; cook them in any
            order.
          </span>
        </p>
        {viewOrder}
      </>
    ) : (
      <OrderCardNote icon="truck">Everything for {meals} arrives today. Cook them in any order.</OrderCardNote>
    )
  } else if (now === 'onhand') {
    body = (
      <OrderCardNote icon="cal">
        Cook them in any order. <NextLine order={order} placed={placed} next={next} />.
      </OrderCardNote>
    )
  }

  return (
    <OrderCard
      number={order.number}
      look="placed"
      chip={<Chip icon="bag">Order placed</Chip>}
      title={placedTitle(order, top, placed, today)}
      subtitle={`${coverage(order)} · ${meals}`}
      onOpen={onOpen}
    >
      <div className={styles.track}>
        <PlacedTrack order={order} today={today} />
      </div>
      {body && (
        <>
          <OrderCardDivider />
          <OrderCardBody>{body}</OrderCardBody>
        </>
      )}
      <OrderCardDivider />
      <button
        type="button"
        className={`${styles.fold} ${mealsOpen ? styles.open : ''}`}
        aria-expanded={mealsOpen}
        onClick={onToggleMeals}
      >
        {!mealsOpen && <MealStack count={order.meals.length} />}
        <span className={styles.foldLabel}>{meals}</span>
        <span className={styles.chevron} aria-hidden="true">
          <Icon name="chevD" />
        </span>
      </button>
      {mealsOpen && (
        <div className={styles.rows}>
          {order.meals.map((meal) => (
            <MealRow key={meal.id} meal={meal} onOpen={() => onOpenMeal(meal.id)} rated />
          ))}
        </div>
      )}
    </OrderCard>
  )
}

/** The order's track: in its own view, with the date under each step. */
export function PlacedTrack({
  order,
  today,
  userId,
  dated = false,
}: {
  order: PlacedOrder
  today: ISODate
  userId?: string
  dated?: boolean
}) {
  const days = daysBetween(order.delivery_date, order.next_delivery_date)
  const on = placedOn(order)
  const dates: TrackDate[] | undefined = dated
    ? [
        { date: formatDate(on, 'short'), note: plural(order.meals.length, 'meal') },
        { date: formatDate(on, 'short') },
        { date: formatDate(on, 'short'), note: `by ${byWhom(order.placed_by, userId)}` },
        {
          date: formatDate(order.delivery_date, 'short'),
          note: phase(order, today) === 'arriving' ? 'today' : undefined,
        },
        { date: span(order.delivery_date, addDays(order.next_delivery_date, -1)) },
      ]
    : undefined
  return (
    <OrderTrack
      phase={phase(order, today)}
      days={days}
      day={daysBetween(order.delivery_date, today) + 1}
      dates={dates}
    />
  )
}

/** What comes after an order's meals: the next order and when its groceries come. */
export function NextLine({ order, placed, next }: { order: PlacedOrder; placed: PlacedOrder[]; next: NextOrder }) {
  const date = <b>{formatDate(order.next_delivery_date, 'short')}</b>
  const following = followingOrder(order, placed, next)
  if (following?.kind === 'placed') {
    return (
      <>
        Order {following.order.number} arrives {date}
      </>
    )
  }
  if (following?.kind === 'next') {
    return (
      <>
        Order {following.next.number} is due {date} (not placed yet)
      </>
    )
  }
  return <>Next delivery {date}</>
}

/** "Sep 26–28", or "Sep 29–Oct 2" across months. */
function span(first: ISODate, last: ISODate): string {
  const sameMonth = first.slice(0, 7) === last.slice(0, 7)
  const end = sameMonth ? String(Number(last.slice(8))) : formatDate(last, 'monthDay')
  return `${formatDate(first, 'monthDay')}–${end}`
}
