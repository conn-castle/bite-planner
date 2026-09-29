import { haptics } from '../lib/haptics'
import styles from './Stepper.module.css'

interface StepperProps {
  value: number
  min: number
  max: number
  onChange: (value: number) => void
  /** Names the value for assistive technology, e.g. "Servings". */
  label: string
  /** In a row of a group: on the fill, a little smaller. */
  inRow?: boolean
  /** Across its container on the fill, − and + at the ends (a product's tile). */
  wide?: boolean
  /** How much − and + change the value (1 by default). */
  step?: number
  /** How the value reads, e.g. "1½". */
  format?: (value: number) => string
  /** Greyed out at its value, and can't change (e.g. its item is off the list). */
  disabled?: boolean
}

/** A − value + control. The value animates as it changes. */
export function Stepper({
  value,
  min,
  max,
  onChange,
  label,
  inRow,
  wide,
  step: by = 1,
  format = String,
  disabled = false,
}: StepperProps) {
  function step(delta: number) {
    // Rounded, so steps of ¼ don't drift.
    const next = Math.min(max, Math.max(min, Math.round((value + delta * by) * 100) / 100))
    if (next === value) return
    haptics.tap()
    onChange(next)
  }

  return (
    <div
      className={[styles.stepper, inRow && styles.inRow, wide && styles.wide, disabled && styles.disabled].filter(Boolean).join(' ')}
      role="group"
      aria-label={label}
      aria-disabled={disabled}
    >
      <button
        type="button"
        className={`${styles.step} cc-hit`}
        onClick={() => step(-1)}
        disabled={disabled || value <= min}
        aria-label={`Fewer ${label.toLowerCase()}`}
      >
        −
      </button>
      <output className={styles.value} aria-live="polite" key={value}>
        {format(value)}
      </output>
      <button
        type="button"
        className={`${styles.step} cc-hit`}
        onClick={() => step(1)}
        disabled={disabled || value >= max}
        aria-label={`More ${label.toLowerCase()}`}
      >
        +
      </button>
    </div>
  )
}
