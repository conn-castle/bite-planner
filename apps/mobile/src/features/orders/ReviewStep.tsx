import type { GroceryOrder, ISODate } from '../../lib/api/types'
import { formatDate } from '../../lib/dates'
import { haptics } from '../../lib/haptics'
import { MealArt } from '../../ui/art'
import { Button } from '../../ui/Button'
import { Chip } from '../../ui/Chip'
import { Group, ListRow, Row } from '../../ui/Group'
import { Icon } from '../../ui/Icon'
import { Notice } from '../../ui/Notice'
import { formatMinutes } from '../meals/facts'
import { pickedMeals } from './batches'
import { addDays, coveredDays } from './dates'
import { FlowFrame, FooterSummary } from './FlowFrame'
import { plural } from './format'
import { useEditsPending, usePlaceOrder } from './orders'
import styles from './ReviewStep.module.css'

interface ReviewStepProps {
  order: GroceryOrder
  /** Both dates, already saved. */
  delivery: ISODate
  next: ISODate
  onOpenMeal: (mealId: string) => void
  onBack: () => void
  onClose: () => void
}

/** Step 4: the meals and dates at a glance, then Place order. */
export function ReviewStep({ order, delivery, next, onOpenMeal, onBack, onClose }: ReviewStepProps) {
  const place = usePlaceOrder()
  // Placing waits until every change is saved, so the order placed is the one shown.
  const saving = useEditsPending()
  const meals = pickedMeals(order)
  const days = coveredDays(delivery, next).length

  function placeOrder() {
    place.mutate({ orderId: order.id }, { onSuccess: () => haptics.success(), onError: () => haptics.error() })
  }

  return (
    <FlowFrame
      step={3}
      onBack={onBack}
      onClose={onClose}
      title="Review"
      lede={`Order ${order.number}: ${plural(meals.length, 'meal')}, arriving ${formatDate(delivery, 'long')}.`}
      footer={
        <>
          <FooterSummary title={plural(meals.length, 'meal')} detail={`Arrives ${formatDate(delivery, 'medium')}`} />
          <Button variant="accent" large onClick={placeOrder} disabled={saving || meals.length === 0}>
            {place.isPending ? 'Placing…' : 'Place order'}
          </Button>
        </>
      }
    >
      {place.isError && (
        <div className={styles.notice}>
          <Notice>Couldn't place the order: {place.error.message}</Notice>
        </div>
      )}
      {/* Groups are cards at the tab screens' inset, not the flow's text inset. */}
      <div className={styles.groups}>
        <Group title="Dates">
          <Row label="Delivery" detail={formatDate(delivery, 'medium')} />
          <Row label="Next delivery" detail={formatDate(next, 'medium')} />
          <Row
            label="Covers"
            detail={`${formatDate(delivery, 'short')} – ${formatDate(addDays(next, -1), 'short')} · ${plural(days, 'day')}`}
          />
        </Group>
        <Group title="Meals">
          {meals.map((meal) => {
            const minutes = meal.total_time_minutes ?? meal.active_time_minutes
            return (
              <ListRow
                key={meal.id}
                art={<MealArt />}
                overline={minutes != null ? formatMinutes(minutes) : undefined}
                label={meal.name}
                onClick={() => onOpenMeal(meal.id)}
              />
            )
          })}
        </Group>
      </div>
    </FlowFrame>
  )
}

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
            {plural(order.meal_ids.length, 'meal')} arriving {formatDate(delivery, 'long')}, covering you through{' '}
            {formatDate(addDays(next, -1), 'short')}. Order {order.number + 1} is due {formatDate(next, 'medium')}.
          </p>
        )}
      </div>
    </FlowFrame>
  )
}
