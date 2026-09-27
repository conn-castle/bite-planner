import styles from './Stats.module.css'

/** A few numbers side by side, each with what it counts (spec/mockup.html: .lsum). */
export function Stats({ stats }: { stats: { value: string | number; label: string }[] }) {
  return (
    <div className={styles.stats}>
      {stats.map(({ value, label }) => (
        <div key={label}>
          <b>{value}</b>
          <small>{label}</small>
        </div>
      ))}
    </div>
  )
}
