import type { ReactNode } from 'react'
import styles from './FieldLabel.module.css'

/** A small heading over a step's field, with a note at its end if given (spec/mockup.html: .lab). */
export function FieldLabel({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <p className={styles.label}>
      <span>{children}</span>
      {aside && <span className={styles.aside}>{aside}</span>}
    </p>
  )
}
