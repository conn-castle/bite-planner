import type { ISODate, Member, NextOrder, PlacedOrder } from '../../lib/api/types'
import { addDays, dateRange, daysBetween, formatDate, localDate, relativeDay } from '../../lib/dates'
import type { BarDay, DayMark } from '../../ui/DateBar'
import type { TrackPhase } from '../../ui/OrderTrack'
import { newestBatchState, suggestions } from '../orders/batches'
import { needsAttention } from '../orders/booking'
import { lockedByOther } from '../orders/lock'
import { memberName, plural } from '../orders/format'

/**
 * Home: the household's orders around today. The day strip runs from 10 days
 * ago (or the first delivery of the orders still covering them) to 90 days
 * ahead; the cards show the next step first.
 */

const STRIP_PAST_DAYS = 10
const STRIP_FUTURE_DAYS = 90

/** The first day Home asks placed orders for: those covering it or later days. */
export function coveringFrom(today: ISODate): ISODate {
  return addDays(today, -STRIP_PAST_DAYS)
}

/** Where a placed order is, on a day: its groceries on the way, arriving, its meals on hand, or finished. */
export function phase(order: Pick<PlacedOrder, 'delivery_date' | 'next_delivery_date'>, today: ISODate): TrackPhase {
  if (today < order.delivery_date) return 'waiting'
  if (today === order.delivery_date) return 'arriving'
  return today < order.next_delivery_date ? 'onhand' : 'done'
}

/**
 * How pressing the next order is: the first order (nothing to decide by),
 * past its decide-by date, due today or tomorrow, its reminders started, or
 * not yet.
 */
export type Urgency = 'first' | 'late' | 'soon' | 'remind' | 'calm'

export function urgency(next: Pick<NextOrder, 'decide_by_date' | 'reminders_start_date'>, today: ISODate): Urgency {
  const decideBy = next.decide_by_date
  if (decideBy === null) return 'first'
  if (decideBy < today) return 'late'
  if (decideBy <= addDays(today, 1)) return 'soon'
  const start = next.reminders_start_date
  return start !== null && start <= today ? 'remind' : 'calm'
}

/**
 * Whether the next order's daily reminder goes out on a day: from the day they
 * start, every day until it's placed (after its decide-by date that's only
 * known through today).
 */
export function remindsOn(
  next: Pick<NextOrder, 'decide_by_date' | 'reminders_start_date'>,
  date: ISODate,
  today: ISODate,
): boolean {
  const start = next.reminders_start_date
  const decideBy = next.decide_by_date
  if (start === null || decideBy === null) return false
  return start <= date && (date <= decideBy || date <= today)
}

/** The placed order whose meals cover the day. */
export function coverOf(placed: PlacedOrder[], date: ISODate): PlacedOrder | undefined {
  return placed.find((order) => order.delivery_date <= date && date < order.next_delivery_date)
}

/** The last day any placed order's meals cover. */
export function lastCovered(placed: PlacedOrder[]): ISODate | null {
  return placed.reduce<ISODate | null>((last, order) => {
    const covered = addDays(order.next_delivery_date, -1)
    return last === null || covered > last ? covered : last
  }, null)
}

/** The calendar day, on the phone, that an order was placed. */
export function placedOn(order: Pick<PlacedOrder, 'placed_at'>): ISODate {
  return localDate(new Date(order.placed_at))
}

/** The strip's days: which order's meals cover each, and what happens on it. */
export function homeStrip(placed: PlacedOrder[], next: NextOrder, today: ISODate): BarDay[] {
  const start = placed.reduce((first, order) => (order.delivery_date < first ? order.delivery_date : first), coveringFrom(today))
  return dateRange(start, addDays(today, STRIP_FUTURE_DAYS)).map((date) => {
    const cover = coverOf(placed, date)
    const marks: DayMark[] = placed
      .filter((order) => order.delivery_date === date)
      .map((order): DayMark => ({ kind: 'delivery', order: order.number, placed: true }))
    if (next.target_date === date) marks.push({ kind: 'delivery', order: next.number, placed: false })
    if (next.decide_by_date === date) marks.push({ kind: 'decide', late: date < today })
    else {
      if (remindsOn(next, date, today)) marks.push({ kind: 'reminder' })
      if (placed.some((order) => placedOn(order) === date)) marks.push({ kind: 'decided' })
    }
    return {
      date,
      coveredBy: cover?.number ?? null,
      coverAhead: cover !== undefined && phase(cover, today) === 'waiting',
      selectable: true,
      past: date < today,
      tag: date === today ? 'today' : null,
      marks,
    }
  })
}

/**
 * What the strip says about a day: its order's color (a number) and what
 * happens on it. `reminderTime` is when the member's own reminder goes out ("8:00 AM").
 */
export function dayCaption(
  placed: PlacedOrder[],
  next: NextOrder,
  date: ISODate,
  today: ISODate,
  reminderTime: string | null,
): { order: number | null; lines: string[] } {
  const lines: string[] = []
  const cover = coverOf(placed, date)
  let order = cover?.number ?? null
  if (cover) {
    const day = daysBetween(cover.delivery_date, date) + 1
    const days = daysBetween(cover.delivery_date, cover.next_delivery_date)
    lines.push(`Order ${cover.number} meals · day ${day} of ${days}`)
  }
  for (const arriving of placed.filter((o) => o.delivery_date === date)) {
    order ??= arriving.number
    lines.push(`Order ${arriving.number} groceries arrive`)
  }
  if (next.target_date === date) {
    order ??= next.number
    lines.push(`Order ${next.number} target delivery (not placed)`)
  }
  if (next.decide_by_date === date) lines.push(`Decide Order ${next.number}`)
  if (remindsOn(next, date, today)) lines.push(reminderTime ? `Reminder ${reminderTime}` : 'Reminder')
  for (const decided of placed.filter((o) => placedOn(o) === date)) lines.push(`Order ${decided.number} placed`)
  if (lines.length === 0) lines.push(placed.length > 0 || next.draft ? 'No meals covered' : 'Nothing planned yet')
  return { order, lines }
}

/** A card on Home: a placed order, or the next order (its draft, once started). */
export type HomeCard = { kind: 'placed'; order: PlacedOrder; top: boolean } | { kind: 'next'; top: boolean }

/**
 * The cards, the next step first: an approved order the store couldn't book
 * (it needs someone) > another member placing the next order > suggestions being made > a draft with ideas > the first order > overdue >
 * due today or tomorrow > reminders started > delivery today > groceries on
 * the way > not due yet. Meals on hand are never the next step; that card sits
 * under the top one. Finished orders leave Home. `userId` is the signed-in member.
 */
export function homeCards(
  placed: PlacedOrder[],
  next: NextOrder,
  today: ISODate,
  userId: string | undefined,
): HomeCard[] {
  const ranked: { card: HomeCard; rank: number; date: ISODate | null }[] = placed
    // Finished orders leave Home, unless the store couldn't book one: it needs someone still.
    .filter((order) => phase(order, today) !== 'done' || needsAttention(order.book_state))
    .map((order) => ({
      card: { kind: 'placed', order, top: false },
      // One the store couldn't book needs someone first.
      rank: needsAttention(order.book_state) ? 0.5 : { waiting: 9, arriving: 8, onhand: 9.5, done: 99 }[phase(order, today)],
      date: order.delivery_date,
    }))
  ranked.push({ card: { kind: 'next', top: false }, rank: nextRank(next, today, userId), date: next.target_date })
  ranked.sort((a, b) => a.rank - b.rank || (a.date ?? '9999').localeCompare(b.date ?? '9999'))

  const cards = ranked.map(({ card }) => card)
  const top = cards.find(
    (card) => card.kind === 'next' || phase(card.order, today) !== 'onhand' || needsAttention(card.order.book_state),
  )
  return top ? [{ ...top, top: true }, ...cards.filter((card) => card !== top)] : cards
}

function nextRank(next: NextOrder, today: ISODate, userId: string | undefined): number {
  if (next.draft) {
    if (lockedByOther(next.draft, userId)) return 1
    if (newestBatchState(next.draft).status === 'running') return 2
    if (suggestions(next.draft).length > 0) return 3
  }
  return { first: 4, late: 5, soon: 6, remind: 7, calm: 10 }[urgency(next, today)]
}

/** Today's order's meals ("3 meals") and the last day they cover, if an order covers today. */
export function onHand(placed: PlacedOrder[], today: ISODate): { meals: string; through: ISODate } | null {
  const cover = coverOf(placed, today)
  return cover ? { meals: plural(cover.meals.length, 'meal'), through: addDays(cover.next_delivery_date, -1) } : null
}

/** "Sat 26 – Mon 28": the days an order's meals cover. */
export function coverage(order: Pick<PlacedOrder, 'delivery_date' | 'next_delivery_date'>): string {
  return `${formatDate(order.delivery_date, 'short')} – ${formatDate(addDays(order.next_delivery_date, -1), 'short')}`
}

/** Who did something, as said after "by": "you", or their name. */
export function byWhom(member: Member, userId: string | undefined): string {
  return member.user_id === userId ? 'you' : memberName(member, userId)
}

/** A placed order card's title: when its groceries come, or its meals on hand. The top card says how long they last. */
export function placedTitle(order: PlacedOrder, top: boolean, placed: PlacedOrder[], today: ISODate): string {
  const now = phase(order, today)
  if (now === 'waiting') {
    const through = lastCovered(placed) ?? addDays(order.next_delivery_date, -1)
    return top
      ? `You're set through ${relativeDay(through, today, 'long')}`
      : `Arrives ${relativeDay(order.delivery_date, today, 'long')}`
  }
  if (now === 'arriving') return 'Groceries arrive today'
  const meals = plural(order.meals.length, 'meal')
  return now === 'onhand' ? `${meals} on hand` : meals
}

/** The order after a placed one: placed too (and on Home), or the next order, or neither (it's older). */
export type Following = { kind: 'placed'; order: PlacedOrder } | { kind: 'next'; next: NextOrder } | null

export function followingOrder(order: PlacedOrder, placed: PlacedOrder[], next: NextOrder | undefined): Following {
  const after = placed.find((other) => other.number === order.number + 1)
  if (after) return { kind: 'placed', order: after }
  return next?.number === order.number + 1 ? { kind: 'next', next } : null
}
