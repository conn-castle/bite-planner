import type { GroceryOrder } from '../../lib/api/types'
import { Button } from '../../ui/Button'
import { Chip } from '../../ui/Chip'
import { OrderCard, OrderCardLine } from '../../ui/OrderCard'
import { newestBatchState, suggestions } from './batches'
import styles from './DraftCard.module.css'
import { memberName, plural } from './format'

interface DraftCardProps {
  order: GroceryOrder
  /** The signed-in member, so their own draft reads "You started it". */
  userId: string | undefined
  onContinue: () => void
  onDiscard: () => void
  discarding: boolean
}

/** The household's unplaced order: where it's at, and continue or discard it. */
export function DraftCard({ order, userId, onContinue, onDiscard, discarding }: DraftCardProps) {
  const ideas = suggestions(order).length
  const picks = order.meal_ids.length
  const working = newestBatchState(order).status === 'running'
  const look = ideas > 0 ? 'ready' : working ? 'working' : 'draft'

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
    look === 'ready' ? (
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
      <OrderCardLine icon="users">
        {memberName(order.created_by, userId)} started it. Saved for the household; anyone can continue it.
      </OrderCardLine>
      <div className={styles.actions}>
        <Button variant={look === 'ready' ? 'accent' : 'primary'} block onClick={onContinue}>
          Continue order
        </Button>
        <button type="button" className={`${styles.discard} cc-hit`} onClick={onDiscard} disabled={discarding}>
          Discard draft
        </button>
      </div>
    </OrderCard>
  )
}
