import type { ISODate } from './api/types'

/**
 * Calendar days ("2026-09-28") as the phone shows them. They have no time of
 * day, so they are read as local midnight and formatted in the phone's locale.
 */

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

/** A day in the phone's locale: "Monday, Sep 28" (long), "Mon, Sep 28" (medium), "Mon 28" (short). */
export function formatDate(date: ISODate, style: 'long' | 'medium' | 'short', locale?: string): string {
  const day = toLocalDate(date)
  if (style === 'short') {
    return `${day.toLocaleDateString(locale, { weekday: 'short' })} ${day.getDate()}`
  }
  return day.toLocaleDateString(locale, {
    weekday: style === 'long' ? 'long' : 'short',
    month: 'short',
    day: 'numeric',
  })
}
