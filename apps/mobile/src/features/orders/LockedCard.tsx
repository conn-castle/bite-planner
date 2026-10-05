import type { GroceryOrder, OrderLock } from '../../lib/api/types'
import { formatDate } from '../../lib/dates'
import { theirName, timeAgo } from '../../lib/format'
import { useNow } from '../../lib/useNow'
import { Avatar } from '../../ui/Avatar'
import { Chip } from '../../ui/Chip'
import { OrderCard, OrderCardBody, OrderCardLine } from '../../ui/OrderCard'
import { StepDots } from '../../ui/StepDots'
import { defaultDeliveryDate } from './dates'
import styles from './LockedCard.module.css'
import { STEP_NAMES, stepDoing, stepIndex } from './steps'

interface LockedCardProps {
  order: GroceryOrder
  /** Another member's: they're placing the order. */
  lock: OrderLock
}

/** The next order while another member is placing it: who, where they are, and that reminders go on. No actions. */
export function LockedCard({ order, lock }: LockedCardProps) {
  const now = useNow(30_000)
  const who = theirName(lock.member)
  const at = stepIndex(lock.step)
  const delivery = defaultDeliveryDate(order)
  const forDelivery = delivery === null ? '' : ` for ${formatDate(delivery, 'weekday')}'s delivery`
  const doing = stepDoing(lock.step)

  return (
    <OrderCard
      number={order.number}
      look="locked"
      chip={
        <Chip tone="dark">
          <Avatar name={lock.member.name} size="xs" />
          {who} is ordering
        </Chip>
      }
      title={`${who} is placing an order`}
      aside={<Avatar name={lock.member.name} size="md" />}
      subtitle={`${who} is ${doing}${forDelivery}. Only one person can order at a time; you'll see Order ${order.number} here once it's placed.`}
    >
      <OrderCardBody>
        <div className={styles.step}>
          <StepDots
            steps={STEP_NAMES}
            at={at}
            look="other"
            label={
              <>
                Started {timeAgo(new Date(lock.since), now)} · <b>{STEP_NAMES[at]}</b>
              </>
            }
          />
        </div>
        {order.decide_by_date !== null && delivery !== null && (
          <OrderCardLine icon="cal">
            Decide by <b>{formatDate(order.decide_by_date, 'short')}</b> · delivery {formatDate(delivery, 'short')}
          </OrderCardLine>
        )}
        <OrderCardLine icon="bell">Reminders keep going to everyone daily until it's placed</OrderCardLine>
      </OrderCardBody>
    </OrderCard>
  )
}
