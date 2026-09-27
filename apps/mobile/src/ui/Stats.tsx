import styles from './Stats.module.css'

/** A few numbers side by side, each with what it counts (spec/mockup.html: .lsum). */
export function Stats({
  stats,
  className,
}: {
  stats: { value: string | number; label: string }[]
  /** Where it sits, e.g. no space above it at the top of a card. */
  className?: string
}) {
  return (
    <div className={[styles.stats, className].filter(Boolean).join(' ')}>
      {stats.map(({ value, label }) => (
        <div key={label}>
          <b>{value}</b>
          <small>{label}</small>
        </div>
      ))}
    </div>
  )
}
