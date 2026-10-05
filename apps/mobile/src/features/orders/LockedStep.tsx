import type { GroceryOrder } from '../../lib/api/types'
import { theirName, timeAgo } from '../../lib/format'
import { useNow } from '../../lib/useNow'
import { Avatar } from '../../ui/Avatar'
import { Button } from '../../ui/Button'
import { StepDots } from '../../ui/StepDots'
import { FlowFrame, FooterSummary } from './FlowFrame'
import type { LockedOut } from './lock'
import styles from './LockedStep.module.css'
import { STEP_NAMES, stepIndex } from './steps'

interface LockedStepProps {
  order: GroceryOrder
  lockedOut: NonNullable<LockedOut>
  onClose: () => void
  onTakeOver: () => void
}

/**
 * The flow while someone else is placing the order: who, and where they are.
 * It opens the draft by itself once they're done (the lock ends); the member's
 * own other phone can be taken over at once.
 */
export function LockedStep({ order, lockedOut, onClose, onTakeOver }: LockedStepProps) {
  const now = useNow(30_000)
  const { lock } = lockedOut
  const at = stepIndex(lock.step)
  const ago = timeAgo(new Date(lock.since), now)
  const you = lockedOut.by === 'you elsewhere'
  const who = theirName(lock.member)

  return (
    <FlowFrame
      step={null}
      onClose={onClose}
      footer={
        you ? (
          <>
            <FooterSummary title="Order here instead?" detail="The other phone stops ordering." />
            <Button large onClick={onTakeOver}>
              Order here
            </Button>
          </>
        ) : (
          <>
            <FooterSummary detail="Nothing for you to do right now." />
            <Button large onClick={onClose}>
              OK
            </Button>
          </>
        )
      }
    >
      <div className={styles.locked}>
        <div className={styles.who}>
          <Avatar name={lock.member.name} size="lg" />
        </div>
        <h2 className={styles.title}>{you ? "You're placing this order on another phone" : `${who} is placing an order`}</h2>
        <p className={styles.lede}>
          {you
            ? `Only one phone can order at a time. You opened Order ${order.number} there ${ago}.`
            : `Only one person in the household can order at a time. ${who} started Order ${order.number} ${ago}.`}
        </p>
        <div className={styles.step}>
          <StepDots steps={STEP_NAMES} at={at} look="other" label={<b>{STEP_NAMES[at]}</b>} />
        </div>
        {!you && (
          <>
            <p className={`${styles.lede} ${styles.small} ${styles.after}`}>
              Order {order.number} shows up on Home as soon as {who} places it. If {who} closes it without placing, or{' '}
              {who}'s app goes away for 2 minutes, you can pick up {who}'s draft where it left off.
            </p>
            <p className={`${styles.lede} ${styles.small}`}>Reminders keep going to everyone until the order is placed.</p>
          </>
        )}
      </div>
    </FlowFrame>
  )
}
