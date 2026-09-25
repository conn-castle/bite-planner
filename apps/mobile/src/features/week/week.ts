import { localDate } from '../../lib/dates'
import type { BarDay } from '../../ui/DateBar'

/** The seven days (Sunday to Saturday, local time) of the week containing `date`. */
export function daysOfWeek(date: Date): Date[] {
  const sunday = new Date(date.getFullYear(), date.getMonth(), date.getDate() - date.getDay())
  return Array.from(
    { length: 7 },
    (_, index) => new Date(sunday.getFullYear(), sunday.getMonth(), sunday.getDate() + index),
  )
}

/** This week as a date bar: today tagged, past days greyed out. */
export function weekBar(today: Date): BarDay[] {
  const todayDate = localDate(today)
  return daysOfWeek(today).map((day) => {
    const date = localDate(day)
    return { date, coveredBy: null, selectable: date >= todayDate, tag: date === todayDate ? 'today' : null }
  })
}
