import type { GroceryOrder, ISODate, OrderDates } from '../../lib/api/types'
import { addDays, dateRange, daysBetween, formatDate, toLocalDate } from '../../lib/dates'
import type { BarDay } from '../../ui/DateBar'
import { plural } from './format'

/** An order's dates: calendar days ("2026-09-28"), with the arithmetic in lib/dates.ts. */

// Delivery days offered past the default, so there is a choice either way.
// The delivery days run this long past the last day with meals (or today, once that has passed).
const DAYS_PAST_COVERED = 30
const NEXT_DELIVERY_CHOICES_PAST_DEFAULT = 7

type DeliveryLimits = Pick<GroceryOrder, 'target_date' | 'earliest_delivery_date'>

/**
 * The target date (the previous order's next delivery), or the earliest
 * delivery date once the target has passed. The first order has no default.
 */
export function defaultDeliveryDate({ target_date, earliest_delivery_date }: DeliveryLimits): ISODate | null {
  if (target_date === null) return null
  return target_date < earliest_delivery_date ? earliest_delivery_date : target_date
}

/** The delivery date to show: the saved one while it is still possible, else the default. */
export function shownDeliveryDate(order: DeliveryLimits & Pick<GroceryOrder, 'delivery_date'>): ISODate | null {
  const saved = order.delivery_date
  return saved !== null && saved >= order.earliest_delivery_date ? saved : defaultDeliveryDate(order)
}

/** One day per picked meal after the delivery date (at least one day). */
export function defaultNextDeliveryDate(delivery: ISODate, picks: number): ISODate {
  return addDays(delivery, Math.max(1, picks))
}

/** The next delivery date to show: the saved one while it is after the delivery date, else the default. */
export function shownNextDeliveryDate(delivery: ISODate, saved: ISODate | null, picks: number): ISODate {
  return saved !== null && saved > delivery ? saved : defaultNextDeliveryDate(delivery, picks)
}

/**
 * The dates to save when the delivery date changes: a next delivery date on or
 * before the new delivery date no longer makes sense, so it is cleared.
 */
export function datesForDelivery(delivery: ISODate, next: ISODate | null): OrderDates {
  return { delivery_date: delivery, next_delivery_date: next !== null && next > delivery ? next : null }
}

/** The days an order covers: its delivery date up to the day before the next delivery. */
export function coveredDays(delivery: ISODate, next: ISODate): ISODate[] {
  return dateRange(delivery, addDays(next, -1))
}

/**
 * How a delivery date lines up with the previous order, whose meals last
 * until the target date: 0 when it arrives on the target date, the number of
 * days with no meals when later, negative (days of overlap) when earlier.
 * Null for the first order.
 */
export function deliveryGap(target: ISODate | null, delivery: ISODate): number | null {
  return target === null ? null : daysBetween(target, delivery)
}

function latest(first: ISODate, ...others: (ISODate | null)[]): ISODate {
  return others.reduce<ISODate>((last, date) => (date !== null && date > last ? date : last), first)
}

type DeliveryBarOrder = Pick<GroceryOrder, 'number' | 'target_date' | 'earliest_delivery_date' | 'delivery_date'>

/**
 * The days to choose this order's delivery from. The bar starts at the last
 * day the previous order's meals cover (the day before the target date), or
 * at today once that has passed, so the user sees where they are; today
 * itself can't be chosen. It runs 30 days past where it starts (the last
 * covered day, or today), further if the chosen date is later, and
 * back to a chosen date that is earlier than the start.
 */
export function deliveryBar(order: DeliveryBarOrder, today: ISODate): BarDay[] {
  const { number, target_date: target, earliest_delivery_date: earliest } = order
  const shown = shownDeliveryDate(order)
  const lastCovered = target === null ? null : addDays(target, -1)
  const start = lastCovered !== null && lastCovered > today ? lastCovered : today
  const first = shown !== null && shown < start ? shown : start
  const last = latest(addDays(start, DAYS_PAST_COVERED), target, shown)
  const targetPassed = target !== null && target < earliest
  return dateRange(first, last).map((date) => ({
    date,
    coveredBy: target !== null && date < target ? number - 1 : null,
    selectable: date >= earliest,
    tag:
      date === today
        ? 'today'
        : date === target && !targetPassed
          ? 'target'
          : targetPassed && date === earliest
            ? 'soonest'
            : null,
  }))
}

/**
 * The days to choose the next delivery from. The bar starts at this order's
 * delivery date (not selectable: the next delivery comes after it) and runs a
 * week past the recommended date, further if the chosen date is later. The
 * days this order's meals cover, up to the chosen next delivery, are marked.
 */
export function nextDeliveryBar(order: Pick<GroceryOrder, 'number'>, delivery: ISODate, picks: number, shown: ISODate): BarDay[] {
  const recommended = defaultNextDeliveryDate(delivery, picks)
  const last = latest(addDays(recommended, NEXT_DELIVERY_CHOICES_PAST_DEFAULT), shown)
  return dateRange(delivery, last).map((date) => ({
    date,
    coveredBy: date < shown ? order.number : null,
    selectable: date > delivery,
    tag: date === delivery ? 'delivery' : date === recommended ? 'best' : null,
  }))
}

// What the dates mean, as the Delivery date and Next delivery steps say it.

/** Where the delivery date comes from: the target date, the soonest delivery (the target passed), or the member's pick. */
export function deliveryChoice(order: DeliveryLimits, delivery: ISODate): 'target' | 'soonest' | 'pick' {
  const { target_date: target, earliest_delivery_date: earliest } = order
  if (delivery === target) return 'target'
  if (target !== null && target < earliest && delivery === earliest) return 'soonest'
  return 'pick'
}

/** What the delivery date means next to the previous order's days, in the phone's locale (or `locale`). */
export function deliveryWhy(
  order: Pick<GroceryOrder, 'number' | 'target_date'>,
  delivery: ISODate,
  meals: number,
  locale?: string,
): string {
  const previous = order.number - 1
  const gap = deliveryGap(order.target_date, delivery)
  if (gap === null) {
    return `Your ${plural(meals, 'meal')} start ${toLocalDate(delivery).toLocaleDateString(locale, { weekday: 'long' })}.`
  }
  if (gap === 0) {
    return `Starts the day after Order ${previous}'s last covered day, ${formatDate(addDays(delivery, -1), 'short', locale)}.`
  }
  if (gap > 0) {
    // The days from the target date up to this delivery.
    const days = dateRange(addDays(delivery, -gap), addDays(delivery, -1))
    return `${plural(gap, 'day')} with no meals covered before it arrives (${dayRange(days, locale)}).`
  }
  return `Arrives while Order ${previous} still covers ${plural(-gap, 'day')}. Groceries may overlap.`
}

/** How the meals fit the days until the next delivery. */
export function nextDeliveryWhy(delivery: ISODate, next: ISODate, meals: number, locale?: string): string {
  const days = coveredDays(delivery, next)
  const span = dayRange(days, locale)
  if (days.length === meals) return `${plural(meals, 'meal')}, one a day, cover ${span}.`
  const extra =
    days.length > meals
      ? `${plural(days.length - meals, 'day')} ${days.length - meals === 1 ? 'needs' : 'need'} leftovers or takeout.`
      : `${plural(meals - days.length, 'meal')} ${meals - days.length === 1 ? 'carries' : 'carry'} past the next delivery.`
  return `${plural(meals, 'meal')} cover ${plural(days.length, 'day')}, ${span}. ${extra}`
}

/**
 * What the next delivery sets for the order after this one: its decide-by date
 * (the household's offset before it) and when reminders to decide it start
 * (the household's lead before that), as the backend works them out.
 */
export function followingDates(
  number: number,
  next: ISODate,
  offset: number,
  lead: number,
  today: ISODate,
  locale?: string,
): string {
  const decideBy = addDays(next, -offset)
  const start = addDays(decideBy, -lead)
  return `Order ${number}: decide by ${formatDate(decideBy, 'medium', locale)}, reminders start ${
    start <= today ? 'today' : formatDate(start, 'medium', locale)
  }.`
}

/** "Mon 28 – Wed 30", or "Mon 28" for one day. */
export function dayRange(days: ISODate[], locale?: string): string {
  const first = days[0]
  const last = days[days.length - 1]
  if (first === undefined || last === undefined) return ''
  const day = (date: ISODate) => formatDate(date, 'short', locale)
  return first === last ? day(first) : `${day(first)} – ${day(last)}`
}
