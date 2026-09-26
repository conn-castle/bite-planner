import styles from './TimeField.module.css'

interface TimeFieldProps {
  /** Hours and minutes: "08:00". */
  value: string
  onChange: (value: string) => void
  /** Names it for assistive technology, e.g. "Notification time". */
  label: string
}

/** A time of day, chosen with the phone's own time picker. */
export function TimeField({ value, onChange, label }: TimeFieldProps) {
  return (
    <input
      type="time"
      className={styles.input}
      value={value}
      aria-label={label}
      // Cleared (iOS's picker has a Reset): there's always a time, so it keeps the one it had.
      onChange={(event) => event.target.value && onChange(event.target.value)}
    />
  )
}
