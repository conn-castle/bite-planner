import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Button } from './Button'
import { Icon, type IconName } from './Icon'
import styles from './InfoDialog.module.css'

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

interface InfoDialogProps {
  isOpen: boolean
  onClose: () => void
  icon: IconName
  title: string
  children: ReactNode
  footer?: ReactNode
  dismissible?: boolean
  /** Feedback may focus its field; informational dialogs keep container focus. */
  initialFocus?: 'dialog' | 'input'
}

/**
 * More about something on the screen, on request: a small dialog in the middle
 * of the screen, closed with Got it or a tap outside it. A React portal over
 * everything (not an Ionic modal: inside the order flow, Ionic moves a modal's
 * content where its taps never reach React).
 */
export function InfoDialog({ isOpen, onClose, icon, title, children, footer, dismissible = true, initialFocus = 'dialog' }: InfoDialogProps) {
  const dialog = useRef<HTMLDivElement>(null)
  // Where it goes: inside the Ionic modal it's opened from (e.g. the order flow), whose focus
  // trap would otherwise pull focus back out of it; else the page's body.
  const [host, setHost] = useState<HTMLElement | null>(null)
  const [pageShown, setPageShown] = useState<boolean>(true)
  const anchor = useCallback((node: HTMLSpanElement | null) => {
    if (!node) return
    setHost(node.closest<HTMLElement>('ion-modal') ?? document.body)
    // IonNav keeps the previous page mounted. Its dialog must wait until that
    // page is shown again, including after accepting a recipe on top of List.
    const page: HTMLElement | null = node.closest<HTMLElement>('.ion-page')
    if (!page) return
    setPageShown(!page.classList.contains('ion-page-hidden'))
    const enter = (): void => setPageShown(true)
    const leave = (): void => setPageShown(false)
    // A cancelled swipe fires only "will" events; keep the current page's
    // dialogs usable until navigation actually completes.
    page.addEventListener('ionViewDidEnter', enter)
    page.addEventListener('ionViewDidLeave', leave)
    return () => {
      page.removeEventListener('ionViewDidEnter', enter)
      page.removeEventListener('ionViewDidLeave', leave)
    }
  }, [])
  const open: boolean = isOpen && pageShown
  // The latest close, so re-renders while it's open don't move the focus.
  const close = useRef(onClose)
  useEffect(() => {
    close.current = () => { if (dismissible) onClose() }
  })

  // A modal for the keyboard too: focus moves into it, Tab stays in it, Escape
  // closes it, and focus goes back to what opened it.
  useEffect(() => {
    if (!open) return
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const input = initialFocus === 'input' ? dialog.current?.querySelector<HTMLElement>('textarea, input') : null
    if (input) input.focus()
    else dialog.current?.focus()
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        // Only the dialog: not the order flow under it (Ionic closes it on Escape too).
        event.preventDefault()
        event.stopPropagation()
        close.current()
        return
      }
      if (event.key !== 'Tab' || dialog.current === null) return
      const focusable = [...dialog.current.querySelectorAll<HTMLElement>(FOCUSABLE)]
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const at = document.activeElement
      if (event.shiftKey && (at === first || at === dialog.current)) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (at === last || !dialog.current.contains(at))) {
        event.preventDefault()
        first.focus()
      }
    }
    // Captured, so it's handled before anything under the dialog sees the key.
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('keydown', onKey, true)
      opener?.focus()
    }
  }, [open, initialFocus])

  const shown =
    open &&
    host !== null &&
    createPortal(
    <div className={styles.backdrop} onClick={() => { if (dismissible) onClose() }}>
      <div
        ref={dialog}
        tabIndex={-1}
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
        {footer ?? <Button block onClick={onClose}>Got it</Button>}
      </div>
    </div>,
    host,
  )
  return (
    <>
      <span ref={anchor} hidden />
      {shown}
    </>
  )
}
