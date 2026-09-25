import styles from './Countdown.module.css'

const RADIUS = 25
const CIRCLE = 2 * Math.PI * RADIUS
// Days over which the ring fills: it's nearly full the day before.
const SPAN_DAYS = 5

interface CountdownProps {
  /** Days left until the date: 0 is today, negative once it has passed. */
  days: number
  /** Accent when it's time to act; quiet before. */
  tone: 'accent' | 'quiet'
}

/** A ring that fills as a date nears, with the days left in it, or how late it is (spec/mockup.html: .cd). */
export function Countdown({ days, tone }: CountdownProps) {
  const late = days < 0
  const filled = late ? 1 : Math.max(0.08, 1 - days / SPAN_DAYS)
  const label = late ? `${-days} ${-days === 1 ? 'day' : 'days'} late` : days === 0 ? 'Today' : `${days} ${days === 1 ? 'day' : 'days'} left`
  return (
    <div className={`${styles.countdown} ${late ? styles.late : tone === 'accent' ? styles.accent : ''}`} role="img" aria-label={label}>
      <svg viewBox="0 0 58 58" aria-hidden="true">
        <circle cx="29" cy="29" r={RADIUS} className={styles.track} />
        <circle cx="29" cy="29" r={RADIUS} className={styles.fill} strokeDasharray={`${CIRCLE * filled} ${CIRCLE}`} />
      </svg>
      <span className={styles.value} aria-hidden="true">
        <b>{late ? -days : days}</b>
        <small>{late ? 'late' : days === 0 ? 'today' : days === 1 ? 'day' : 'days'}</small>
      </span>
    </div>
  )
}
