import { type CSSProperties, useLayoutEffect, useRef } from 'react'
import type { ISODate } from '../lib/api/types'
import { formatDate, toLocalDate } from '../lib/dates'
import { Icon } from './Icon'
import styles from './DateBar.module.css'
import { orderColor } from './orderColor'

/** What a day in a date bar is marked with, under its date. */
export type DayTag = 'today' | 'target' | 'soonest' | 'best' | 'delivery'

/**
 * What happens on a day, as a small mark under it (compact strip only): an
 * order's groceries arrive (in its color once placed), the day to decide the
 * next order by (red once passed), a reminder to decide it goes out, or an
 * order was placed.
 */
export type DayMark =
  | { kind: 'delivery'; order: number; placed: boolean }
  | { kind: 'decide'; late: boolean }
  | { kind: 'reminder' }
  | { kind: 'decided' }

/** One day in a date bar. */
export interface BarDay {
  date: ISODate
  /** The number of the order whose meals cover this day; null when no meals do. */
  coveredBy: number | null
  /** Its meals' groceries haven't arrived yet: the bar is a lighter tint (compact strip only). */
  coverAhead?: boolean
  /** Whether the day can be chosen (or, in a bar without choices, isn't past); the others are greyed out. */
  selectable: boolean
  /** Before today: greyed out, though it can still be chosen (compact strip only). */
  past?: boolean
  tag: DayTag | null
  marks?: DayMark[]
}

const TAG_TEXT: Record<DayTag, string> = {
  today: 'Today',
  target: 'Target',
  soonest: 'Soonest',
  best: 'Best',
  delivery: 'Delivery',
}

// Tags on days that orient rather than suggest (they are not the app's pick).
const QUIET_TAGS: ReadonlySet<DayTag> = new Set(['today', 'delivery'])

// Days the compact strip shows before the highlighted one when it first appears.
const DAYS_BEFORE_SELECTED = 2

function markText(mark: DayMark): string {
  if (mark.kind === 'delivery') return `Order ${mark.order} ${mark.placed ? 'delivery' : 'target delivery'}`
  if (mark.kind === 'decide') return mark.late ? 'decide-by date, passed' : 'decide-by date'
  if (mark.kind === 'reminder') return 'reminder'
  return 'order placed'
}

interface DateBarProps {
  days: BarDay[]
  /** The highlighted day: the choice, or (in a bar without choices) today. */
  selected: ISODate | null
  /** Called with a chosen day. Without it, the bar only shows the days. */
  onChoose?: (date: ISODate) => void
  /** The name of the group of days, e.g. "Delivery date". */
  label: string
  /** Scroll sideways (the default), or fit every day across the width. */
  scrollable?: boolean
  /**
   * The small calendar strip in a card (home), instead of large chips: a
   * letter for the weekday, today in a filled circle, and each day's marks.
   * It shows no other tags and no legend.
   */
  compact?: boolean
  /** A line under the compact strip, in its card: e.g. what happens on the highlighted day. */
  caption?: DayCaption
}

/** What a compact strip says under it: a day ("Today"), a dot of an order's color, and what happens. */
export interface DayCaption {
  day: string
  order: number | null
  text: string
}

/**
 * Every row of days in the app: days with meals have a bar in their order's
 * color, days that can't be chosen are greyed out, and a tag under a date
 * marks today, the target, or the recommended day.
 */
export function DateBar({
  days,
  selected,
  onChoose,
  label,
  scrollable = true,
  compact = false,
  caption,
}: DateBarProps) {
  const strip = useRef<HTMLDivElement>(null)
  const positioned = useRef(false)

  // Keep the highlighted day in view, from the start and whenever it changes;
  // a day already in view stays put. The compact strip first shows a couple of
  // days before it, so today appears with the days just past.
  useLayoutEffect(() => {
    const bar = strip.current
    const day = bar?.querySelector<HTMLElement>('[data-selected="true"]')
    if (!scrollable || !bar || !day) return
    if (!compact || positioned.current) {
      day.scrollIntoView({ block: 'nearest', inline: 'nearest' })
      return
    }
    const position = () => {
      // Measured against the strip itself; an element's offsetLeft is against its nearest positioned ancestor.
      const start = day.getBoundingClientRect().left - bar.getBoundingClientRect().left + bar.scrollLeft
      const padding = parseFloat(getComputedStyle(bar).paddingLeft)
      bar.scrollLeft = start - padding - DAYS_BEFORE_SELECTED * day.offsetWidth
      positioned.current = true
    }
    if (bar.clientWidth > 0) return position()
    // Not laid out yet (e.g. on a page still hidden): position it once it is.
    const observer = new ResizeObserver(() => {
      if (bar.clientWidth === 0) return
      observer.disconnect()
      position()
    })
    observer.observe(bar)
    return () => observer.disconnect()
  }, [scrollable, compact, selected])

  const orders = [...new Set(days.flatMap((day) => (day.coveredBy === null ? [] : [day.coveredBy])))]
  const fit: CSSProperties = scrollable ? {} : { gridTemplateColumns: `repeat(${days.length}, minmax(0, 1fr))` }

  const bar = (
    <div
      ref={strip}
      className={`${compact ? styles.compact : styles.chips} ${scrollable ? styles.scroll : styles.fit}`}
      style={fit}
      role={onChoose ? 'radiogroup' : 'list'}
      aria-label={label}
    >
      {days.map(({ date, coveredBy, coverAhead = false, selectable, past = false, tag, marks = [] }, index) => {
        const isSelected = date === selected
        const cover: CSSProperties & { '--cover'?: string } = coveredBy === null ? {} : { '--cover': orderColor(coveredBy) }
        const className = [
          styles.day,
          isSelected && styles.selected,
          (!selectable || past) && styles.unavailable,
          compact && tag === 'today' && styles.today,
        ]
          .filter(Boolean)
          .join(' ')
        const description = [
          formatDate(date, 'long'),
          tag && TAG_TEXT[tag],
          coveredBy !== null && `meals from Order ${coveredBy}`,
          ...(compact ? marks.map(markText) : []),
          onChoose && !selectable && 'not available',
        ]
          .filter(Boolean)
          .join(', ')
        const day = toLocalDate(date)
        // The compact strip names the month on its first day, and joins the bars of days an order covers.
        const firstOfMonth = compact && day.getDate() === 1
        const joined = (neighbor: BarDay | undefined) => coveredBy !== null && neighbor?.coveredBy === coveredBy
        const content = compact ? (
          <>
            <small className={firstOfMonth ? styles.month : undefined}>
              {firstOfMonth
                ? day.toLocaleDateString(undefined, { month: 'short' }).toUpperCase()
                : day.toLocaleDateString(undefined, { weekday: 'narrow' })}
            </small>
            <b>{day.getDate()}</b>
            <span className={styles.marks} aria-hidden="true">
              {marks.map((mark, markIndex) => (
                <DayMarkIcon key={markIndex} mark={mark} />
              ))}
            </span>
            <i
              className={[
                styles.cover,
                coverAhead && styles.ahead,
                joined(days[index - 1]) && styles.joinStart,
                joined(days[index + 1]) && styles.joinEnd,
              ]
                .filter(Boolean)
                .join(' ')}
              aria-hidden="true"
            />
          </>
        ) : (
          <>
            <small>{day.toLocaleDateString(undefined, { weekday: 'short' })}</small>
            <b>{day.getDate()}</b>
            {coveredBy !== null && <i className={styles.cover} aria-hidden="true" />}
            {tag && (
              <span className={`${styles.tag} ${QUIET_TAGS.has(tag) ? styles.quiet : ''}`} aria-hidden="true">
                {TAG_TEXT[tag]}
              </span>
            )}
          </>
        )
        return onChoose ? (
          <button
            key={date}
            type="button"
            role="radio"
            aria-checked={isSelected}
            aria-label={description}
            data-selected={isSelected}
            disabled={!selectable}
            className={className}
            style={cover}
            onClick={() => !isSelected && onChoose(date)}
          >
            {content}
          </button>
        ) : (
          <div
            key={date}
            role="listitem"
            aria-label={description}
            aria-current={isSelected ? 'date' : undefined}
            data-selected={isSelected}
            className={`${className} ${styles.static}`}
            style={cover}
          >
            {content}
          </div>
        )
      })}
    </div>
  )

  if (compact) {
    return (
      <div className={styles.box}>
        {bar}
        {caption && (
          <p className={styles.caption} aria-live="polite">
            {caption.order !== null && <i className={styles.dot} style={{ background: orderColor(caption.order) }} />}
            <b>{caption.day}</b>
            <span className={styles.captionText}>{caption.text}</span>
          </p>
        )}
      </div>
    )
  }
  return (
    <>
      {bar}
      {orders.length > 0 && (
        <div className={styles.legend}>
          {orders.map((order) => (
            <span key={order}>
              <i style={{ background: orderColor(order) }} />
              Covered by Order {order}
            </span>
          ))}
        </div>
      )}
    </>
  )
}

function DayMarkIcon({ mark }: { mark: DayMark }) {
  if (mark.kind === 'delivery') {
    return (
      <span className={styles.truck} style={mark.placed ? { color: orderColor(mark.order) } : undefined}>
        <Icon name="truck" />
      </span>
    )
  }
  if (mark.kind === 'decide') return <span className={`${styles.decide} ${mark.late ? styles.late : ''}`} />
  if (mark.kind === 'reminder') return <span className={styles.reminder} />
  return <span className={styles.decided} />
}
