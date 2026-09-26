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
}

/** A − value + control. The value animates as it changes. */
export function Stepper({ value, min, max, onChange, label, inRow }: StepperProps) {
  function step(delta: number) {
    const next = Math.min(max, Math.max(min, value + delta))
    if (next === value) return
    haptics.tap()
    onChange(next)
  }

  return (
    <div className={`${styles.stepper} ${inRow ? styles.inRow : ''}`} role="group" aria-label={label}>
      <button
        type="button"
        className={`${styles.step} cc-hit`}
        onClick={() => step(-1)}
        disabled={value <= min}
        aria-label={`Fewer ${label.toLowerCase()}`}
      >
        −
      </button>
      <output className={styles.value} aria-live="polite" key={value}>
        {value}
      </output>
      <button
        type="button"
        className={`${styles.step} cc-hit`}
        onClick={() => step(1)}
        disabled={value >= max}
        aria-label={`More ${label.toLowerCase()}`}
      >
        +
      </button>
    </div>
  )
}
