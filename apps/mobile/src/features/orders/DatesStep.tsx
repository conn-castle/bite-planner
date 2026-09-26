import { useState } from 'react'
import type { GroceryOrder, ISODate } from '../../lib/api/types'
import { SERVINGS } from '../../lib/api/limits'
import { addDays, dateRange, formatDate, localDate, toLocalDate } from '../../lib/dates'
import { haptics } from '../../lib/haptics'
import { useNow } from '../../lib/useNow'
import { Button } from '../../ui/Button'
import { DateBar } from '../../ui/DateBar'
import { Group, Row } from '../../ui/Group'
import { Notice } from '../../ui/Notice'
import { Stepper } from '../../ui/Stepper'
import { MealRow } from '../meals/MealRow'
import { useHousehold } from '../settings/settings'
import { pickedMeals } from './batches'
import {
  coveredDays,
  datesForDelivery,
  defaultNextDeliveryDate,
  deliveryBar,
  deliveryGap,
  nextDeliveryBar,
  shownDeliveryDate,
  shownNextDeliveryDate,
} from './dates'
import styles from './DatesStep.module.css'
import { FlowFrame, FooterSummary } from './FlowFrame'
import { plural } from './format'
import { useEditsPending, usePlaceOrder, useSetDates, useSetServings } from './orders'

// How often the step checks the time, so "today" moves on at midnight.
const TODAY_CHECK_MS = 60_000

type Open = 'delivery' | 'next' | null

interface DatesStepProps {
  order: GroceryOrder
  onOpenMeal: (mealId: string, servings: number) => void
  onBack: () => void
  onClose: () => void
}

/**
 * Step 2: when the order arrives, when the next one does, and how much of each
 * meal to make, then Place order. The dates are rows that open their day row in
 * place; they start at the target date and one day per meal.
 */
export function DatesStep({ order, onOpenMeal, onBack, onClose }: DatesStepProps) {
  const household = useHousehold().shown
  const setDates = useSetDates()
  const setServings = useSetServings()
  const place = usePlaceOrder()
  // Placing waits until every change is saved, so the order placed is the one shown.
  const saving = useEditsPending()
  const today = localDate(useNow(TODAY_CHECK_MS))
  const meals = pickedMeals(order)
  const delivery = shownDeliveryDate(order)
  // With no delivery date yet (the first order), its day row starts open.
  const [open, setOpen] = useState<Open>(delivery === null ? 'delivery' : null)
  const next = delivery === null ? null : shownNextDeliveryDate(delivery, order.next_delivery_date, meals.length)
  const servings = meals.reduce((total, pick) => total + pick.servings, 0)
  // The newest change that failed.
  const failed = [setDates, setServings, place]
    .filter((change) => change.isError)
    .sort((a, b) => b.submittedAt - a.submittedAt)[0]?.error

  function toggle(row: Exclude<Open, null>) {
    haptics.tap()
    setOpen((current) => (current === row ? null : row))
  }

  function chooseDelivery(date: ISODate) {
    haptics.tap()
    setDates.mutate({ orderId: order.id, dates: datesForDelivery(date, order.next_delivery_date) })
  }

  function chooseNext(date: ISODate) {
    if (delivery === null) return
    haptics.tap()
    setDates.mutate({ orderId: order.id, dates: { delivery_date: delivery, next_delivery_date: date } })
  }

  function placeOrder() {
    if (delivery === null || next === null) return
    // The dates shown are saved with it: they may be untouched defaults.
    place.mutate(
      { orderId: order.id, dates: { delivery_date: delivery, next_delivery_date: next } },
      { onSuccess: () => haptics.success(), onError: () => haptics.error() },
    )
  }

  return (
    <FlowFrame
      step={1}
      onBack={onBack}
      onClose={onClose}
      title="Dates and servings"
      lede={`Order ${order.number}: when it arrives, when Order ${order.number + 1} does, and how much of each meal to make.`}
      footer={
        <>
          <FooterSummary
            title={`${plural(meals.length, 'meal')} · ${plural(servings, 'serving')}`}
            detail={delivery ? `Arrives ${formatDate(delivery, 'medium')}` : 'Choose a delivery date'}
          />
          <Button variant="accent" large onClick={placeOrder} disabled={delivery === null || saving || place.isPending}>
            {place.isPending ? 'Placing…' : 'Place order'}
          </Button>
        </>
      }
    >
      {failed && (
        <div className={styles.notice}>
          <Notice>Couldn't save that: {failed.message}</Notice>
        </div>
      )}
      {/* Groups are cards at the tab screens' inset, not the flow's text inset. */}
      <div className={styles.groups}>
        <Group title="Dates">
          <Row
            label="Delivery"
            sub={deliveryLabel(order, delivery)}
            detail={delivery ? formatDate(delivery, 'medium') : 'Choose'}
            onClick={() => toggle('delivery')}
            expanded={open === 'delivery'}
          />
          {open === 'delivery' && (
            <div className={styles.days}>
              <DateBar days={deliveryBar(order, today)} selected={delivery} onChoose={chooseDelivery} label="Delivery date" />
              <p className={styles.why}>{deliveryWhy(order, delivery, meals.length)}</p>
            </div>
          )}
          <Row
            label="Next delivery"
            sub={delivery && next ? coverage(delivery, next, meals.length) : 'After the delivery date'}
            detail={next ? formatDate(next, 'medium') : '–'}
            onClick={() => toggle('next')}
            disabled={delivery === null}
            expanded={open === 'next'}
          />
          {open === 'next' && delivery && next && (
            <div className={styles.days}>
              <DateBar
                days={nextDeliveryBar(order, delivery, meals.length, next)}
                selected={next}
                onChoose={chooseNext}
                label="Next delivery date"
              />
              <p className={styles.why}>
                Covers {dayRange(coveredDays(delivery, next))}.
                {next === defaultNextDeliveryDate(delivery, meals.length) ? ' Recommended: one day per meal.' : ''}
              </p>
              {household && (
                <p className={styles.why}>
                  {followingDates(order.number + 1, next, household.decide_by_offset_days, household.reminder_lead_days, today)}
                </p>
              )}
            </div>
          )}
        </Group>
        <Group
          title="Meals"
          footerIcon="users"
          footer="Servings start at the household's default, in Settings. The shopping list will be made for these."
        >
          {meals.map(({ meal, servings: count }) => (
            <MealRow
              key={meal.id}
              meal={meal}
              onOpen={() => onOpenMeal(meal.id, count)}
              control={
                <Stepper
                  inRow
                  value={count}
                  min={SERVINGS.min}
                  max={SERVINGS.max}
                  label="Servings"
                  onChange={(value) => setServings.mutate({ orderId: order.id, mealId: meal.id, servings: value })}
                />
              }
            />
          ))}
        </Group>
      </div>
    </FlowFrame>
  )
}

/**
 * What the next delivery sets for the order after this one: its decide-by date
 * (the household's offset before it) and when reminders to decide it start
 * (the household's lead before that), as the backend works them out.
 */
function followingDates(number: number, next: ISODate, offset: number, lead: number, today: ISODate): string {
  const decideBy = addDays(next, -offset)
  const start = addDays(decideBy, -lead)
  return `Order ${number}: decide by ${formatDate(decideBy, 'medium')}, reminders start ${
    start <= today ? 'today' : formatDate(start, 'medium')
  }.`
}

/** Where the delivery date comes from. */
function deliveryLabel(order: GroceryOrder, delivery: ISODate | null): string {
  const { target_date: target, earliest_delivery_date: earliest } = order
  if (delivery === null) return 'Choose the day groceries arrive'
  if (delivery === target) return 'Target date'
  if (target !== null && target < earliest && delivery === earliest) return 'Soonest delivery'
  return 'Your pick'
}

/** What the delivery date means next to the previous order's days. */
function deliveryWhy(order: GroceryOrder, delivery: ISODate | null, meals: number): string {
  const previous = order.number - 1
  if (delivery === null) return 'Choose the day your groceries arrive.'
  const gap = deliveryGap(order.target_date, delivery)
  if (gap === null) {
    return `Your ${plural(meals, 'meal')} start ${toLocalDate(delivery).toLocaleDateString(undefined, { weekday: 'long' })}.`
  }
  if (gap === 0) return `Starts the day after Order ${previous}'s last covered day, ${formatDate(addDays(delivery, -1), 'short')}.`
  if (gap > 0) {
    // The days from the target date up to this delivery.
    return `${plural(gap, 'day')} with no meals covered before it arrives (${dayRange(dateRange(addDays(delivery, -gap), addDays(delivery, -1)))}).`
  }
  return `Arrives while Order ${previous} still covers ${plural(-gap, 'day')}. Groceries may overlap.`
}

/** How the meals fit the days until the next delivery. */
function coverage(delivery: ISODate, next: ISODate, meals: number): string {
  const days = coveredDays(delivery, next).length
  const fit =
    days === meals
      ? 'one meal a day'
      : days > meals
        ? `${plural(days - meals, 'day')} of leftovers or takeout`
        : `${plural(meals - days, 'meal')} to spare`
  return `Covers ${plural(days, 'day')} · ${fit}`
}

/** "Mon 28 – Wed 30", or "Mon 28" for one day. */
function dayRange(days: ISODate[]): string {
  const first = days[0]
  const last = days[days.length - 1]
  if (first === undefined || last === undefined) return ''
  return first === last ? formatDate(first, 'short') : `${formatDate(first, 'short')} – ${formatDate(last, 'short')}`
}
