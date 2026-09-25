import type { GroceryOrder, ISODate } from '../../lib/api/types'
import { formatDate } from '../../lib/dates'
import { Button } from '../../ui/Button'
import { Chip } from '../../ui/Chip'
import { MealStack } from '../../ui/MealStack'
import { OrderCard, OrderCardBody, OrderCardLine } from '../../ui/OrderCard'
import { newestBatchState, suggestions } from './batches'
import styles from './DraftCard.module.css'
import { memberName, plural } from './format'

interface DraftCardProps {
  order: GroceryOrder
  /** The signed-in member, so their own draft reads "You started it". */
  userId: string | undefined
  today: ISODate
  onContinue: () => void
  onDiscard: () => void
  discarding: boolean
}

/** The household's unplaced order: where it's at, when to decide by, and continue or discard it. */
export function DraftCard({ order, userId, today, onContinue, onDiscard, discarding }: DraftCardProps) {
  const ideas = suggestions(order).length
  const picks = order.meal_ids.length
  const working = newestBatchState(order).status === 'running'
  const decideBy = order.decide_by_date
  const late = decideBy !== null && decideBy < today
  const look = ideas > 0 ? (late ? 'late' : 'ready') : working ? 'working' : 'draft'

  const title =
    picks > 0
      ? 'Finish your order'
      : ideas > 0
        ? `${plural(ideas, 'meal idea')} ready`
        : working
          ? 'Your meal ideas are on the way'
          : 'Pick meals'
  const subtitle =
    picks > 0
      ? `${plural(picks, 'meal')} added. Next, choose the delivery dates and review the order.`
      : ideas > 0
        ? "Add the ones you want. Pick as many as you'll cook before the next order."
        : working
          ? "This takes a minute or two. You can leave; they'll be here when you come back."
          : 'No meals added yet.'
  const chip =
    look === 'late' ? (
      <Chip tone="danger" icon="alert">
        Overdue
      </Chip>
    ) : look === 'ready' ? (
      <Chip tone="accent" icon="spark">
        {picks > 0 ? 'In progress' : 'Ready'}
      </Chip>
    ) : look === 'working' ? (
      <Chip tone="accent" icon="spark">
        Working
      </Chip>
    ) : (
      <Chip>Draft</Chip>
    )

  return (
    <OrderCard number={order.number} look={look} chip={chip} title={title} subtitle={subtitle}>
      <OrderCardBody>
        {ideas > 0 && (
          <div className={styles.meals}>
            <MealStack count={picks > 0 ? picks : ideas} large />
          </div>
        )}
        <OrderCardLine icon="users">
          {memberName(order.created_by, userId)} started it. Saved for the household; anyone can continue it.
        </OrderCardLine>
        {decideBy !== null &&
          (late ? (
            <OrderCardLine icon="cal" tone="danger">
              Was due {formatDate(decideBy, 'short')}
            </OrderCardLine>
          ) : (
            <OrderCardLine icon="cal">
              Decide by <b>{formatDate(decideBy, 'short')}</b>
            </OrderCardLine>
          ))}
        <div className={styles.actions}>
          <Button variant={late ? 'danger' : look === 'ready' ? 'accent' : 'primary'} block onClick={onContinue}>
            Continue order
          </Button>
          <button type="button" className={`${styles.discard} cc-hit`} onClick={onDiscard} disabled={discarding}>
            Discard draft
          </button>
        </div>
      </OrderCardBody>
    </OrderCard>
  )
}
