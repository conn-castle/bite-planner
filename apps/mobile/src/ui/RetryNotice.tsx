import { Button } from './Button'
import { Notice } from './Notice'
import styles from './RetryNotice.module.css'

/**
 * Work that didn't finish (meal ideas, the list, shopping), said plainly, with
 * Try again as a button of its own under it. What went wrong in detail is for
 * the logs, not the screen.
 */
export function RetryNotice({ message, onRetry, busy = false }: { message: string; onRetry: () => void; busy?: boolean }) {
  return (
    <div className={styles.retry}>
      <Notice>{message}</Notice>
      <Button variant="secondary" block onClick={onRetry} disabled={busy}>
        Try again
      </Button>
    </div>
  )
}
