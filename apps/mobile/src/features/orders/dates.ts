import type { GroceryOrder, ISODate, OrderDates } from '../../lib/api/types'
import { addDays, dateRange, daysBetween } from '../../lib/dates'
import type { BarDay } from '../../ui/DateBar'

/** An order's dates: calendar days ("2026-09-28"), with the arithmetic in lib/dates.ts. */

// Delivery days offered past the default, so there is a choice either way.
const DELIVERY_CHOICES = 14
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
 * itself can't be chosen. It runs two weeks past the earliest delivery date,
 * further if the target or the chosen date is later, and back to a chosen
 * date that is earlier than the start.
 */
export function deliveryBar(order: DeliveryBarOrder, today: ISODate): BarDay[] {
  const { number, target_date: target, earliest_delivery_date: earliest } = order
  const shown = shownDeliveryDate(order)
  const lastCovered = target === null ? null : addDays(target, -1)
  const start = lastCovered !== null && lastCovered > today ? lastCovered : today
  const first = shown !== null && shown < start ? shown : start
  const last = latest(addDays(earliest, DELIVERY_CHOICES - 1), target, shown)
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
