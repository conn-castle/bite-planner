import { useEffect } from 'react'
import styles from './Toast.module.css'

// How long a toast stays before it slides away.
const SHOW_MS = 4200

interface ToastProps {
  title: string
  body: string
  /** Tapping it (it goes away too). */
  onTap: () => void
  /** Its time is up. */
  onHide: () => void
}

/**
 * A notification shown inside the app while it's open, like the banner iOS
 * shows when it isn't: the app icon, a title with "now", and a line. Render it
 * with a key per notification, so each one gets its own time on screen.
 */
export function Toast({ title, body, onTap, onHide }: ToastProps) {
  useEffect(() => {
    const timer = setTimeout(onHide, SHOW_MS)
    return () => clearTimeout(timer)
  }, [onHide])

  return (
    <button type="button" className={styles.toast} onClick={onTap} role="status">
      <span className={styles.icon} aria-hidden="true">
        <svg viewBox="0 0 24 24">
          <path d="M4 21V9.5l2-1V5h2.5v2H10V5h4v2h1.5V5H18v3.5l2 1V21h-5.5v-4a2.5 2.5 0 00-5 0v4z" />
        </svg>
      </span>
      <span className={styles.text}>
        <b>
          {title}
          <small>now</small>
        </b>
        <span>{body}</span>
      </span>
    </button>
  )
}
