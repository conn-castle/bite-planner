import type { ClockTime, ISODate } from './api/types'

/**
 * Calendar days ("2026-09-28"), such as delivery dates. They have no time of
 * day: the arithmetic is on whole days in UTC, where every day is 24 hours, so
 * a phone's time zone never shifts them; they are shown as local midnight, in
 * the phone's locale.
 */

const DAY_MS = 24 * 60 * 60 * 1000

function toUtcMs(date: ISODate): number {
  const [year, month, day] = date.split('-').map(Number)
  return Date.UTC(year, month - 1, day)
}

export function addDays(date: ISODate, days: number): ISODate {
  return new Date(toUtcMs(date) + days * DAY_MS).toISOString().slice(0, 10)
}

/** Whole days from `from` to `to` (negative when `to` is earlier). */
export function daysBetween(from: ISODate, to: ISODate): number {
  return Math.round((toUtcMs(to) - toUtcMs(from)) / DAY_MS)
}

/** Every day from `first` through `last`, inclusive. */
export function dateRange(first: ISODate, last: ISODate): ISODate[] {
  const count = daysBetween(first, last) + 1
  return Array.from({ length: Math.max(0, count) }, (_, index) => addDays(first, index))
}

/** The day as a local `Date` at midnight, for formatting in the phone's locale. */
export function toLocalDate(date: ISODate): Date {
  const [year, month, day] = date.split('-').map(Number)
  return new Date(year, month - 1, day)
}

/** The calendar day of `now` in the phone's time zone. */
export function localDate(now: Date): ISODate {
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

/**
 * A day in the phone's locale: "Monday, Sep 28" (long), "Mon, Sep 28"
 * (medium), "Mon 28" (short), "Sep 28" (monthDay), or "Monday" (weekday).
 */
export function formatDate(
  date: ISODate,
  style: 'long' | 'medium' | 'short' | 'monthDay' | 'weekday',
  locale?: string,
): string {
  const day = toLocalDate(date)
  if (style === 'short') {
    return `${day.toLocaleDateString(locale, { weekday: 'short' })} ${day.getDate()}`
  }
  if (style === 'monthDay') return day.toLocaleDateString(locale, { month: 'short', day: 'numeric' })
  if (style === 'weekday') return day.toLocaleDateString(locale, { weekday: 'long' })
  return day.toLocaleDateString(locale, {
    weekday: style === 'long' ? 'long' : 'short',
    month: 'short',
    day: 'numeric',
  })
}

/**
 * A day as said from today: "today", "tomorrow", then the weekday within the
 * week ("Saturday"), then "Mon, Oct 5". The short style also has "yesterday",
 * and short weekdays ("Sat").
 */
export function relativeDay(date: ISODate, today: ISODate, style: 'long' | 'short', locale?: string): string {
  const days = daysBetween(today, date)
  if (days === 0) return 'today'
  if (days === 1) return 'tomorrow'
  if (days === -1 && style === 'short') return 'yesterday'
  if (days > 1 && days < 7) {
    return style === 'long'
      ? formatDate(date, 'weekday', locale)
      : toLocalDate(date).toLocaleDateString(locale, { weekday: 'short' })
  }
  return formatDate(date, 'medium', locale)
}

/**
 * A moment (a UTC timestamp) as said from today, in the phone's time zone:
 * "today at 7:52 AM", "yesterday at 8:40 PM", or "on Mon, Sep 21 at 8:40 PM".
 */
export function relativeMoment(at: Date, today: ISODate, locale?: string): string {
  const day = localDate(at)
  const time = at.toLocaleTimeString(locale, { hour: 'numeric', minute: '2-digit' })
  if (day === today) return `today at ${time}`
  if (day === addDays(today, -1)) return `yesterday at ${time}`
  return `on ${formatDate(day, 'medium', locale)} at ${time}`
}

/** A clock time ("08:00:00") in the phone's locale: "8:00 AM". */
export function formatClockTime(time: ClockTime, locale?: string): string {
  const [hours, minutes] = time.split(':').map(Number)
  return new Date(2000, 0, 1, hours, minutes).toLocaleTimeString(locale, { hour: 'numeric', minute: '2-digit' })
}
