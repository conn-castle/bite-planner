import { type CSSProperties, useLayoutEffect, useRef } from 'react'
import type { ISODate } from '../lib/api/types'
import { formatDate, toLocalDate } from '../lib/dates'
import styles from './DateBar.module.css'
import { orderColor } from './orderColor'

/** What a day in a date bar is marked with, under its date. */
export type DayTag = 'today' | 'target' | 'soonest' | 'best' | 'delivery'

/** One day in a date bar. */
export interface BarDay {
  date: ISODate
  /** The number of the order whose meals cover this day; null when no meals do. */
  coveredBy: number | null
  /** Whether the day can be chosen (or, in a bar without choices, isn't past); the others are greyed out. */
  selectable: boolean
  tag: DayTag | null
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
}

/**
 * Every row of days in the app: days with meals have a bar in their order's
 * color, days that can't be chosen are greyed out, and a tag under a date
 * marks today, the target, or the recommended day.
 */
export function DateBar({ days, selected, onChoose, label, scrollable = true }: DateBarProps) {
  const strip = useRef<HTMLDivElement>(null)

  // Show the highlighted day, even when it is far along a scrolling bar.
  useLayoutEffect(() => {
    const element = strip.current
    const chosen = element?.querySelector<HTMLElement>('[data-selected="true"]')
    if (!scrollable || !element || !chosen) return
    if (chosen.offsetLeft + chosen.offsetWidth > element.clientWidth) {
      element.scrollLeft = chosen.offsetLeft - parseFloat(getComputedStyle(element).paddingLeft)
    }
  }, [scrollable])

  const orders = [...new Set(days.flatMap((day) => (day.coveredBy === null ? [] : [day.coveredBy])))]
  const fit: CSSProperties = scrollable ? {} : { gridTemplateColumns: `repeat(${days.length}, minmax(0, 1fr))` }

  return (
    <>
      <div
        ref={strip}
        className={`${styles.days} ${scrollable ? styles.scroll : styles.fit}`}
        style={fit}
        role={onChoose ? 'radiogroup' : 'list'}
        aria-label={label}
      >
        {days.map(({ date, coveredBy, selectable, tag }) => {
          const isSelected = date === selected
          const cover: CSSProperties & { '--cover'?: string } =
            coveredBy === null ? {} : { '--cover': orderColor(coveredBy) }
          const className = [styles.day, isSelected && styles.selected, !selectable && styles.unavailable]
            .filter(Boolean)
            .join(' ')
          const description = [
            formatDate(date, 'long'),
            tag && TAG_TEXT[tag],
            coveredBy !== null && `meals from Order ${coveredBy}`,
            onChoose && !selectable && 'not available',
          ]
            .filter(Boolean)
            .join(', ')
          const content = (
            <>
              <small>{toLocalDate(date).toLocaleDateString(undefined, { weekday: 'short' })}</small>
              <b>{toLocalDate(date).getDate()}</b>
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
