import { Icon } from './Icon'
import styles from './OrderTrack.module.css'

/** Where a placed order is: groceries on the way, arriving today, meals on hand, or finished. */
export type TrackPhase = 'waiting' | 'arriving' | 'onhand' | 'done'

/** What a node of a dated track says under it: a date, and a quieter line. */
export interface TrackDate {
  date: string
  note?: string
}

/** The date under each of the five nodes. */
export type TrackDates = [TrackDate, TrackDate, TrackDate, TrackDate, TrackDate]

interface OrderTrackProps {
  phase: TrackPhase
  /** Days its meals cover. */
  days: number
  /** Covered days begun so far, today included (while its meals are on hand). */
  day: number
  /** The date under each of the five nodes, in the order's own view. */
  dates?: TrackDates
  /** Booked at the store; until it is, Placed is the step it's at (not checked). */
  booked?: boolean
}

const STEPS = ['Meals', 'List', 'Placed', 'Delivery']
const RING_RADIUS = 9.5
const RING = 2 * Math.PI * RING_RADIUS

/**
 * A placed order's progress, in its order's color (the --c and --soft of an
 * order card): meals picked, list made, placed, delivered, then a ring that
 * fills as the covered days pass.
 */
export function OrderTrack({ phase, days, day, dates, booked = true }: OrderTrackProps) {
  const current = !booked ? 2 : phase === 'waiting' || phase === 'arriving' ? 3 : phase === 'onhand' ? 4 : 5
  const passed = phase === 'onhand' ? day : phase === 'done' ? days : 0
  const arriving = phase === 'arriving'
  return (
    <div className={`${styles.track} ${dates ? styles.dated : ''}`}>
      <div className={styles.line}>
        <i style={{ width: `${(Math.min(current, 4) / 4) * 100}%` }} />
      </div>
      {STEPS.map((label, index) => {
        const done = index < current
        const pulsing = index === 3 && arriving && booked
        const className = [styles.node, done && styles.done, index === current && styles.current, pulsing && styles.pulsing]
          .filter(Boolean)
          .join(' ')
        return (
          <div key={label} className={className}>
            <span className={styles.knob}>{done ? <Icon name="check" /> : pulsing ? <Icon name="truck" /> : null}</span>
            <span className={styles.label}>{label}</span>
            {dates && <NodeDate {...dates[index]} />}
          </div>
        )
      })}
      <div className={[styles.node, phase === 'done' && styles.done, phase === 'onhand' && styles.current].filter(Boolean).join(' ')}>
        <svg className={styles.ring} viewBox="0 0 23 23" aria-hidden="true">
          <circle cx="11.5" cy="11.5" r={RING_RADIUS} className={styles.ringTrack} />
          {/* Before the first day, nothing: an empty dash would still draw its round cap as a dot. */}
          {passed > 0 && (
            <circle
              cx="11.5"
              cy="11.5"
              r={RING_RADIUS}
              className={styles.ringFill}
              strokeDasharray={`${(RING * passed) / days} ${RING}`}
            />
          )}
        </svg>
        <span className={styles.label}>{phase === 'onhand' ? `Day ${passed}/${days}` : `${days} days`}</span>
        {dates && <NodeDate {...dates[4]} />}
      </div>
    </div>
  )
}

function NodeDate({ date, note }: TrackDate) {
  return (
    <span className={styles.date}>
      {date}
      {note && <small>{note}</small>}
    </span>
  )
}
