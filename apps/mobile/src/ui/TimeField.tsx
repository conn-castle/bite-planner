import type { ClockTime } from '../lib/api/types'
import styles from './TimeField.module.css'

interface TimeFieldProps {
  /** As the API has it: "08:00:00". */
  value: ClockTime
  onChange: (value: ClockTime) => void
  /** Names it for assistive technology, e.g. "Notification time". */
  label: string
}

/** A time of day, chosen with the phone's own time picker. */
export function TimeField({ value, onChange, label }: TimeFieldProps) {
  return (
    <input
      type="time"
      className={styles.input}
      // The field shows and gives hours and minutes ("08:00").
      value={value.slice(0, 5)}
      aria-label={label}
      // Cleared (iOS's picker has a Reset): there's always a time, so it keeps the one it had.
      onChange={(event) => event.target.value && onChange(`${event.target.value.slice(0, 5)}:00`)}
    />
  )
}
