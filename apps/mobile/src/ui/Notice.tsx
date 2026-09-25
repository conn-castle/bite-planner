import { IonIcon } from '@ionic/react'
import { alertCircle } from 'ionicons/icons'
import type { ReactNode } from 'react'
import styles from './Notice.module.css'

/** An inline problem report, next to the thing that failed, with an optional way forward. */
export function Notice({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className={styles.notice} role="alert">
      <IonIcon icon={alertCircle} className={styles.icon} aria-hidden="true" />
      <div className={styles.body}>
        <p className={styles.text}>{children}</p>
        {action}
      </div>
    </div>
  )
}
