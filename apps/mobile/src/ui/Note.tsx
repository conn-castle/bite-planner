import type { ReactNode } from 'react'
import { Icon, type IconName } from './Icon'
import styles from './Note.module.css'

/** A quiet line with an icon, under or between a step's content (spec/mockup.html: .pick-note, .foot-note). */
export function Note({ icon, children }: { icon: IconName; children: ReactNode }) {
  return (
    <p className={styles.note}>
      <Icon name={icon} />
      <span>{children}</span>
    </p>
  )
}
