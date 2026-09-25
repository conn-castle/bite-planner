import { IonIcon } from '@ionic/react'
import { add, remove } from 'ionicons/icons'
import { haptics } from '../lib/haptics'
import styles from './Stepper.module.css'

interface StepperProps {
  value: number
  min: number
  max: number
  onChange: (value: number) => void
  /** Names the value for assistive technology, e.g. "Servings". */
  label: string
  size?: 'large' | 'compact'
}

/** A − value + control. The value animates as it changes. */
export function Stepper({ value, min, max, onChange, label, size = 'compact' }: StepperProps) {
  function step(delta: number) {
    const next = Math.min(max, Math.max(min, value + delta))
    if (next === value) return
    haptics.tap()
    onChange(next)
  }

  return (
    <div className={`${styles.stepper} ${styles[size]}`} role="group" aria-label={label}>
      <button
        type="button"
        className={styles.step}
        onClick={() => step(-1)}
        disabled={value <= min}
        aria-label={`Fewer ${label.toLowerCase()}`}
      >
        <IonIcon icon={remove} aria-hidden="true" />
      </button>
      <output className={styles.value} aria-live="polite" key={value}>
        {value}
      </output>
      <button
        type="button"
        className={styles.step}
        onClick={() => step(1)}
        disabled={value >= max}
        aria-label={`More ${label.toLowerCase()}`}
      >
        <IonIcon icon={add} aria-hidden="true" />
      </button>
    </div>
  )
}
