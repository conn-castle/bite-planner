import type { GroceryOrder } from '../../lib/api/types'
import { addDays, formatDate } from '../../lib/dates'
import { Button } from '../../ui/Button'
import { Chip } from '../../ui/Chip'
import { Icon } from '../../ui/Icon'
import { FlowFrame } from './FlowFrame'
import { plural } from './format'
import styles from './PlacedStep.module.css'

interface PlacedStepProps {
  order: GroceryOrder
  onClose: () => void
}

/** After placing: what was ordered and what comes next. */
export function PlacedStep({ order, onClose }: PlacedStepProps) {
  const { delivery_date: delivery, next_delivery_date: next } = order
  return (
    <FlowFrame
      step={null}
      onClose={onClose}
      footer={
        <Button block onClick={onClose}>
          Done
        </Button>
      }
    >
      <div className={styles.placed}>
        <span className={styles.check} aria-hidden="true">
          <Icon name="check" />
        </span>
        <div className={styles.status}>
          <Chip tone="success">Order placed</Chip>
        </div>
        <h2 className={styles.title}>Order {order.number} is on its way</h2>
        {delivery && next && (
          <p className={styles.text}>
            {plural(order.picks.length, 'meal')} arriving {formatDate(delivery, 'long')}, covering you through{' '}
            {formatDate(addDays(next, -1), 'short')}. Order {order.number + 1} is due {formatDate(next, 'medium')}.
          </p>
        )}
      </div>
    </FlowFrame>
  )
}
