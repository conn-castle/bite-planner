import type { MealSummary } from '../lib/api/types'
import { MealArt } from './art'
import styles from './MealStack.module.css'

// Pictures shown before the rest are counted.
const SHOWN = 5

interface MealStackProps {
  /** The meals, in order. */
  meals: Pick<MealSummary, 'id' | 'picture'>[]
  /** Larger pictures, e.g. on the next order's card. */
  large?: boolean
}

/** Meals' pictures overlapping in a row, then "+3 more" (spec/mockup.html: .stack-thumbs). */
export function MealStack({ meals, large = false }: MealStackProps) {
  return (
    <span className={`${styles.stack} ${large ? styles.large : ''}`} aria-hidden="true">
      {meals.slice(0, SHOWN).map((meal) => (
        <span key={meal.id} className={styles.meal}>
          <MealArt meal={meal} />
        </span>
      ))}
      {meals.length > SHOWN && <span className={styles.more}>+{meals.length - SHOWN} more</span>}
    </span>
  )
}
