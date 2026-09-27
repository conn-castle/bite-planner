import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Button } from './Button'
import { Icon, type IconName } from './Icon'
import styles from './InfoDialog.module.css'

interface InfoDialogProps {
  isOpen: boolean
  onClose: () => void
  icon: IconName
  title: string
  children: ReactNode
}

/**
 * More about something on the screen, on request: a small dialog in the middle
 * of the screen, closed with Got it or a tap outside it. A React portal on the
 * page's body, over everything (not an Ionic modal: inside the order flow's
 * card, Ionic moves a modal's content where its taps never reach React).
 */
export function InfoDialog({ isOpen, onClose, icon, title, children }: InfoDialogProps) {
  if (!isOpen) return null
  return createPortal(
    <div className={styles.backdrop} onClick={onClose}>
      <div
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <span className={styles.badge} aria-hidden="true">
          <Icon name={icon} />
        </span>
        <h2 className={styles.title}>{title}</h2>
        {children}
        <Button block onClick={onClose}>
          Got it
        </Button>
      </div>
    </div>,
    document.body,
  )
}
