import type { GroceryOrder, ISODate } from '../../lib/api/types'
import { formatDate, relativeMoment } from '../../lib/dates'
import { useNow } from '../../lib/useNow'
import { Button } from '../../ui/Button'
import { Chip } from '../../ui/Chip'
import { MealStack } from '../../ui/MealStack'
import { OrderCard, OrderCardActions, OrderCardBody, OrderCardLine } from '../../ui/OrderCard'
import { ProgressBar } from '../../ui/ProgressBar'
import { useNotifiedOfIdeas, useYourReminder } from '../notifications/notifications'
import { newestBatchState, suggestions } from './batches'
import { defaultDeliveryDate } from './dates'
import styles from './DraftCard.module.css'
import { elapsed, memberName, plural } from './format'

// The meals in a batch of suggestions, as the backend makes them.
const BATCH_SIZE = 20

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
  const reminder = useYourReminder()
  const ideas = suggestions(order).length
  const picks = order.picks.length
  const batch = newestBatchState(order)
  const notified = useNotifiedOfIdeas(batch.status === 'running' ? batch.job.requested_by.user_id : null)
  const working = batch.status === 'running'
  // Ticks each second while a batch is being made, for the time it has taken.
  const now = useNow(working ? 1000 : 60_000)
  const took = batch.status === 'running' ? elapsed(new Date(batch.job.created_at), now) : null
  const decideBy = order.decide_by_date
  const late = decideBy !== null && decideBy < today
  const look = ideas > 0 ? (late ? 'late' : 'ready') : working ? 'working' : 'draft'
  // The delivery it's for: the soonest possible one.
  const delivery = defaultDeliveryDate(order)
  const forDelivery = delivery === null ? 'your first delivery' : `${formatDate(delivery, 'weekday')}'s delivery`

  const title =
    picks > 0
      ? 'Finish your order'
      : ideas > 0
        ? `${plural(ideas, 'meal idea')} ${ideas === 1 ? 'is' : 'are'} ready`
        : working
          ? 'Your meal ideas are on the way'
          : 'Pick meals'
  const subtitle =
    picks > 0
      ? `${plural(picks, 'meal')} added for ${forDelivery}. Next, choose the dates and each meal's servings.`
      : ideas > 0
        ? `Add the ones you want for ${forDelivery}. Pick as many as you'll cook before the next one.`
        : working
          ? `Chef is picking ${BATCH_SIZE} ideas for ${forDelivery}. This takes a minute or two. ${
              notified ? "You can leave; we'll notify you when they're ready." : "You can leave; they'll be here when you come back."
            }`
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
        {look === 'working' && (
          <>
            <ProgressBar className={styles.progress} />
            <p className={styles.took}>Working for {took}</p>
          </>
        )}
        {ideas > 0 && (
          <div className={styles.meals}>
            <MealStack count={picks > 0 ? picks : ideas} large />
          </div>
        )}
        {ideas > 0 && took !== null && (
          <>
            <ProgressBar className={styles.progressMore} />
            <p className={`${styles.took} ${styles.tookMore}`}>
              Generating {BATCH_SIZE} more · working for {took}
            </p>
          </>
        )}
        {look !== 'working' && (
          <OrderCardLine icon="users">
            {memberName(order.created_by, userId)} started it {relativeMoment(new Date(order.created_at), today)}. Saved for
            the household; anyone can continue it.
          </OrderCardLine>
        )}
        {look !== 'working' &&
          decideBy !== null &&
          (late ? (
            <OrderCardLine icon="bell" tone="danger">
              Was due {formatDate(decideBy, 'short')} · reminders keep going daily until it's placed
            </OrderCardLine>
          ) : (
            <OrderCardLine icon="bell">
              Decide by <b>{formatDate(decideBy, 'short')}</b> ·{' '}
              {reminder.off
                ? 'your reminders are off'
                : reminder.time
                  ? `reminders daily at ${reminder.time}`
                  : 'reminders daily'}
            </OrderCardLine>
          ))}
        <OrderCardActions>
          <Button variant={late ? 'danger' : look === 'ready' ? 'accent' : 'primary'} block onClick={onContinue}>
            {picks > 0 ? 'Continue order' : ideas > 0 ? 'Pick meals' : working ? 'Watch progress' : 'Continue order'}
          </Button>
          <button type="button" className={`${styles.discard} cc-hit`} onClick={onDiscard} disabled={discarding}>
            Discard draft
          </button>
        </OrderCardActions>
      </OrderCardBody>
    </OrderCard>
  )
}
