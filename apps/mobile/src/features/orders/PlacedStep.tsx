import type { BookState, GroceryOrder } from '../../lib/api/types'
import { addDays, formatDate } from '../../lib/dates'
import { elapsed, plural } from '../../lib/format'
import { useNow } from '../../lib/useNow'
import { AgentWork } from '../../ui/AgentWork'
import { PlateArt } from '../../ui/art'
import { Button } from '../../ui/Button'
import { Chip } from '../../ui/Chip'
import { Icon, type IconName } from '../../ui/Icon'
import { BOOK_STATE_ICON, BOOK_STATE_LABEL, needsAttention } from './booking'
import { BookingGroup } from './BookingGroup'
import { FlowFrame, FooterSummary } from './FlowFrame'
import { storeName } from './list'
import styles from './PlacedStep.module.css'

interface PlacedStepProps {
  order: GroceryOrder
  /** It's the draft again (to change it): go to this step, else where it got to. */
  onChanged: (step: 'delivery' | 'list' | undefined) => void
  onClose: () => void
}

/**
 * After approving: while it books at the store, the wait ("Booking at
 * Wegmans", which can be closed); otherwise where it is (waiting for its day,
 * booked, or what needs doing), and what can be done about it.
 */
export function PlacedStep({ order, onChanged, onClose }: PlacedStepProps) {
  const now = useNow(1000)
  const store = storeName(order)
  const state = order.book_state ?? 'booked'
  const done = (
    <Button block onClick={onClose}>
      Done
    </Button>
  )

  if (state === 'booking') {
    const book = order.jobs.findLast((job) => job.kind === 'book')
    return (
      <FlowFrame
        step={null}
        onClose={onClose}
        footer={
          <>
            <FooterSummary title="You can close this" detail="You'll get a notification when it's booked." />
            <Button variant="secondary" large onClick={onClose}>
              Close
            </Button>
          </>
        }
      >
        <AgentWork
          art={<PlateArt />}
          title={`Booking at ${store}`}
          took={book ? elapsed(new Date(book.created_at), now) : 'Starting'}
          lede="Filling your cart, choosing the delivery window, and placing the order. This takes about a minute."
        />
      </FlowFrame>
    )
  }

  const { delivery_date: delivery, next_delivery_date: next } = order
  return (
    <FlowFrame step={null} onClose={onClose} footer={done}>
      <div className={styles.placed}>
        <span className={`${styles.mark} ${needsAttention(state) ? styles.attention : ''}`} aria-hidden="true">
          <Icon name={MARK[state]} />
        </span>
        <div className={styles.status}>
          <Chip
            icon={BOOK_STATE_ICON[state]}
            tone={needsAttention(state) ? 'danger' : state === 'booked' ? 'success' : 'default'}
          >
            {BOOK_STATE_LABEL[state]}
          </Chip>
        </div>
        <h2 className={styles.title}>{title(state, order.number)}</h2>
        {delivery && next && (
          <p className={styles.text}>
            {plural(order.picks.length, 'meal')} arriving {formatDate(delivery, 'long')}, covering you through{' '}
            {formatDate(addDays(next, -1), 'short')}. Order {order.number + 1} is due {formatDate(next, 'medium')}.
          </p>
        )}
      </div>
      <div className={styles.booking}>
        <BookingGroup order={{ ...order, book_state: state }} onChanged={onChanged} onCancelled={onClose} />
      </div>
    </FlowFrame>
  )
}

// The large mark over it: its chip's icon, and a check once it's booked.
const MARK: Record<Exclude<BookState, 'booking'>, IconName> = { ...BOOK_STATE_ICON, booked: 'check' }

function title(state: Exclude<BookState, 'booking'>, number: number): string {
  switch (state) {
    case 'waiting':
      return `Order ${number} is approved`
    case 'booked':
      return `Order ${number} is on its way`
    case 'needs_time':
      return `Order ${number} needs a new time`
    case 'needs_product':
      return `Order ${number} needs a product chosen`
    case 'not_booked':
      return `Order ${number} wasn't booked`
    case 'check_instacart':
      return `Check Instacart for Order ${number}`
  }
}
