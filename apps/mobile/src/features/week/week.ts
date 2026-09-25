/** The seven days (Sunday to Saturday, local time) of the week containing `date`. */
export function daysOfWeek(date: Date): Date[] {
  const sunday = new Date(date.getFullYear(), date.getMonth(), date.getDate() - date.getDay())
  return Array.from(
    { length: 7 },
    (_, index) => new Date(sunday.getFullYear(), sunday.getMonth(), sunday.getDate() + index),
  )
}

export function isSameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
  )
}

/** "Sep 21 – 27", or "Sep 28 – Oct 4" across months. */
export function formatWeekRange(days: Date[], locale?: string): string {
  const first = days[0]
  const last = days[days.length - 1]
  const month = (date: Date) => date.toLocaleDateString(locale, { month: 'short' })
  const end = first.getMonth() === last.getMonth() ? `${last.getDate()}` : `${month(last)} ${last.getDate()}`
  return `${month(first)} ${first.getDate()} – ${end}`
}
