import { MealArt } from './art'
import styles from './MealStack.module.css'

// Pictures shown before the rest are counted.
const SHOWN = 5

interface MealStackProps {
  /** How many meals there are. */
  count: number
  /** Larger pictures, e.g. on the next order's card. */
  large?: boolean
}

/** Meals' pictures overlapping in a row, then "+3 more" (spec/mockup.html: .stack-thumbs). */
export function MealStack({ count, large = false }: MealStackProps) {
  const shown = Math.min(count, SHOWN)
  return (
    <span className={`${styles.stack} ${large ? styles.large : ''}`} aria-hidden="true">
      {Array.from({ length: shown }, (_, index) => (
        <span key={index} className={styles.meal}>
          <MealArt />
        </span>
      ))}
      {count > shown && <span className={styles.more}>+{count - shown} more</span>}
    </span>
  )
}
