/** Groups items into day sections, newest first, labeled the way people talk about days. */

export interface DaySection<T> {
  key: string
  label: string
  items: T[]
}

const DAY_MS = 24 * 60 * 60 * 1000

function startOfDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate())
}

/** "Today", "Yesterday", a weekday within the last week, else the date. */
export function dayLabel(date: Date, now: Date, locale?: string): string {
  const days = Math.round((startOfDay(now).getTime() - startOfDay(date).getTime()) / DAY_MS)
  if (days === 0) return 'Today'
  if (days === 1) return 'Yesterday'
  if (days > 1 && days < 7) return date.toLocaleDateString(locale, { weekday: 'long' })
  return date.toLocaleDateString(locale, {
    month: 'long',
    day: 'numeric',
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' }),
  })
}

/** Items must already be sorted newest first. */
export function groupByDay<T>(
  items: T[],
  dateOf: (item: T) => Date,
  now: Date,
  locale?: string,
): DaySection<T>[] {
  const sections: DaySection<T>[] = []
  for (const item of items) {
    const date = dateOf(item)
    const key = startOfDay(date).toISOString()
    const last = sections[sections.length - 1]
    if (last?.key === key) last.items.push(item)
    else sections.push({ key, label: dayLabel(date, now, locale), items: [item] })
  }
  return sections
}
