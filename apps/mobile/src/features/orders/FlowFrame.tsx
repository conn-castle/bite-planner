import { IonContent, IonFooter, IonHeader, IonToolbar } from '@ionic/react'
import { type ReactNode, useContext } from 'react'
import { IconButton } from '../../ui/Button'
import { Icon } from '../../ui/Icon'
import { StepDots } from '../../ui/StepDots'
import { FlowNotice } from './flowNotice'
import styles from './FlowFrame.module.css'
import { STEP_NAMES } from './steps'

interface FlowFrameProps {
  /** Which of the steps this is, for the progress dots; null hides them. */
  step: number | null
  /** Shows a Back button that goes to the previous step. */
  onBack?: () => void
  onClose: () => void
  /** Optional step-specific body sizing; the shared frame otherwise stays full width. */
  bodyClassName?: string
  title?: ReactNode
  lede?: ReactNode
  /** The bar along the bottom: a short summary and the step's main action. */
  footer: ReactNode
  /**
   * The step's problem (a change that couldn't be saved, why it can't go on), in
   * the one place a problem shows: a problem with the whole flow (the order
   * couldn't be refreshed) takes its place, so the screen never shows two.
   */
  notice?: ReactNode
  children?: ReactNode
}

/** One screen of the create-order flow: Back, progress dots, and Close on top; the step's action at the bottom. */
export function FlowFrame({ step, onBack, onClose, bodyClassName, title, lede, footer, notice: stepNotice, children }: FlowFrameProps) {
  const notice = useContext(FlowNotice) ?? stepNotice
  return (
    <>
      <IonHeader className={styles.header}>
        <IonToolbar>
          <div className={styles.bar}>
            {onBack ? (
              <button type="button" className={`${styles.back} bp-hit`} onClick={onBack}>
                <Icon name="chevL" />
                Back
              </button>
            ) : (
              <span className={styles.spacer} />
            )}
            {step !== null && <StepDots steps={STEP_NAMES} at={step} />}
            <IconButton variant="fill" aria-label="Close" onClick={onClose}>
              <Icon name="x" />
            </IconButton>
          </div>
        </IonToolbar>
      </IonHeader>
      {/* Content scrolls on under the glass footer. */}
      <IonContent fullscreen>
        <div className={`${styles.body} ${bodyClassName ?? ''}`}>
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

/** The footer's summary: a bold line (if any) over a quieter one. */
export function FooterSummary({ title, detail }: { title?: ReactNode; detail: ReactNode }) {
  return (
    <p className={styles.summary}>
      {title && <b>{title}</b>}
      {detail}
    </p>
  )
}
