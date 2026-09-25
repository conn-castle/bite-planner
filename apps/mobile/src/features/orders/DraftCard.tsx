import type { GroceryOrder } from '../../lib/api/types'
import { Button } from '../../ui/Button'
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
  const title =
    picks > 0
      ? 'Finish your order'
      : ideas > 0
        ? `${plural(ideas, 'meal idea')} ready`
        : newestBatchState(order).status === 'running'
          ? 'Your meal ideas are on the way'
          : 'Pick meals'

  return (
    <article className={styles.card}>
      <div className={styles.over}>
        Order {order.number}
        <span className={styles.chip}>Draft</span>
      </div>
      <h2 className={styles.title}>{title}</h2>
      <p className={styles.text}>
        {memberName(order.created_by, userId)} started it · {picks ? `${plural(picks, 'meal')} added` : 'no meals added yet'}.
        Saved for the household; anyone can continue it.
      </p>
      <Button block onClick={onContinue}>
        Continue order {order.number}
      </Button>
      <button type="button" className={styles.discard} onClick={onDiscard} disabled={discarding}>
        Discard draft
      </button>
    </article>
  )
}
