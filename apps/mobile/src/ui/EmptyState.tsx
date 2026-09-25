import type { ReactNode } from 'react'
import styles from './EmptyState.module.css'

interface EmptyStateProps {
  art: ReactNode
  title: string
  children?: ReactNode
  action?: ReactNode
}

/** A calm, centered placeholder for a screen with nothing to show yet. */
export function EmptyState({ art, title, children, action }: EmptyStateProps) {
  return (
    <div className={styles.empty}>
      <div className={styles.art} aria-hidden="true">
        {art}
      </div>
      <h2 className={styles.title}>{title}</h2>
      {children && <p className={styles.text}>{children}</p>}
      {action && <div className={styles.action}>{action}</div>}
    </div>
  )
}
