import { IonContent, IonFooter, IonHeader, IonIcon, IonToolbar } from '@ionic/react'
import { chevronBack, close } from 'ionicons/icons'
import { type ReactNode, useContext } from 'react'
import { IconButton } from '../../ui/Button'
import { FlowNotice } from './flowNotice'
import styles from './FlowFrame.module.css'

const STEP_NAMES = ['Pick meals', 'Delivery date', 'Next delivery', 'Review'] as const

interface FlowFrameProps {
  /** Which of the steps this is, for the progress dots; null hides them. */
  step: number | null
  /** Shows a Back button that goes to the previous step. */
  onBack?: () => void
  onClose: () => void
  title?: ReactNode
  lede?: ReactNode
  /** The bar along the bottom: a short summary and the step's main action. */
  footer: ReactNode
  children?: ReactNode
}

/** One screen of the create-order flow: Back, progress dots, and Close on top; the step's action at the bottom. */
export function FlowFrame({ step, onBack, onClose, title, lede, footer, children }: FlowFrameProps) {
  const notice = useContext(FlowNotice)
  return (
    <>
      <IonHeader className={styles.header}>
        <IonToolbar>
          <div className={styles.bar}>
            {onBack ? (
              <button type="button" className={styles.back} onClick={onBack}>
                <IonIcon icon={chevronBack} aria-hidden="true" />
                Back
              </button>
            ) : (
              <span className={styles.spacer} />
            )}
            {step !== null && (
              <div className={styles.dots} role="img" aria-label={`Step ${step + 1} of ${STEP_NAMES.length}: ${STEP_NAMES[step]}`}>
                {STEP_NAMES.map((name, index) => (
                  <i key={name} className={index <= step ? styles.on : undefined} />
                ))}
              </div>
            )}
            <IconButton aria-label="Close" onClick={onClose} className={styles.close}>
              <IonIcon icon={close} aria-hidden="true" />
            </IconButton>
          </div>
        </IonToolbar>
      </IonHeader>
      <IonContent>
        <div className={styles.body}>
          {notice && <div className={styles.notice}>{notice}</div>}
          {title && <h2 className={styles.title}>{title}</h2>}
          {lede && <p className={styles.lede}>{lede}</p>}
          {children}
        </div>
      </IonContent>
      <IonFooter className={styles.footer}>
        <div className={styles.footerBar}>{footer}</div>
      </IonFooter>
    </>
  )
}

/** The footer's summary: a bold line over a quieter one. */
export function FooterSummary({ title, detail }: { title: ReactNode; detail: ReactNode }) {
  return (
    <p className={styles.summary}>
      <b>{title}</b>
      {detail}
    </p>
  )
}
