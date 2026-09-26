import type { ReactNode } from 'react'
import styles from './ChoiceCard.module.css'
import { Icon } from './Icon'

interface ChoiceCardProps {
  /** The app's suggestion (accent, with a spark), or the member's own pick (plain). */
  suggested: boolean
  /** What it is: "Target date", "Recommended", "Your pick". */
  label: string
  /** The choice itself, large: "Sunday, Sep 27". */
  title: ReactNode
  /** What it means. */
  detail: ReactNode
}

/** A step's current choice, and whether it's the one suggested (spec/mockup.html: .rec, .rec.off). */
export function ChoiceCard({ suggested, label, title, detail }: ChoiceCardProps) {
  return (
    <div className={`${styles.card} ${suggested ? '' : styles.own}`}>
      <p className={styles.label}>
        {suggested && <Icon name="spark" />}
        {label}
      </p>
      <p className={styles.title}>{title}</p>
      <p className={styles.detail}>{detail}</p>
    </div>
  )
}
