import { IonIcon } from '@ionic/react'
import { chevronForward } from 'ionicons/icons'
import type { ReactNode } from 'react'
import styles from './Group.module.css'

/** A titled card of related rows. */
export function Group({ title, footer, children }: { title?: string; footer?: ReactNode; children: ReactNode }) {
  return (
    <section className={styles.group}>
      {title && <h2 className={styles.title}>{title}</h2>}
      <div className={styles.card}>{children}</div>
      {footer && <p className={styles.footer}>{footer}</p>}
    </section>
  )
}

interface RowProps {
  icon?: string
  label: ReactNode
  detail?: ReactNode
  /** Makes the row a button. */
  onClick?: () => void
  disabled?: boolean
  tone?: 'default' | 'danger'
  /** Shows a chevron: the row opens something. */
  chevron?: boolean
}

export function Row({ icon, label, detail, onClick, disabled, tone = 'default', chevron }: RowProps) {
  const content = (
    <>
      {icon && (
        <span className={styles.iconTile} aria-hidden="true">
          <IonIcon icon={icon} />
        </span>
      )}
      <span className={styles.label}>{label}</span>
      {detail != null && <span className={styles.detail}>{detail}</span>}
      {chevron && <IonIcon icon={chevronForward} className={styles.chevron} aria-hidden="true" />}
    </>
  )
  const className = `${styles.row} ${tone === 'danger' ? styles.danger : ''}`
  return onClick ? (
    <button type="button" className={`${className} ${styles.pressable}`} onClick={onClick} disabled={disabled}>
      {content}
    </button>
  ) : (
    <div className={className}>{content}</div>
  )
}
