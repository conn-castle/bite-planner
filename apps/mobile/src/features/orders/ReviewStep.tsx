import type { GroceryOrder, ISODate } from '../../lib/api/types'
import { formatDate } from '../../lib/dates'
import { haptics } from '../../lib/haptics'
import { Button } from '../../ui/Button'
import { Group, Row } from '../../ui/Group'
import { Notice } from '../../ui/Notice'
import { MealRow } from '../meals/MealRow'
import { pickedMeals } from './batches'
import { coveredDays, dayRange } from './dates'
import { FlowFrame, FooterSummary } from './FlowFrame'
import { plural } from './format'
import { useOrderSession } from './lock'
import { useEditsPending, usePlaceOrder } from './orders'
import styles from './Steps.module.css'
import { stepIndex } from './steps'

interface ReviewStepProps {
  order: GroceryOrder
  /** The dates shown (untouched defaults included), which are placed with it. */
  delivery: ISODate
  next: ISODate
  onOpenMeal: (mealId: string, servings: number) => void
  /** Goes back to a step to change what it chose. */
  onChange: (step: 'servings' | 'delivery' | 'next') => void
  onBack: () => void
  onClose: () => void
}

/** The order as it will be placed: its meals and servings, and its dates; then Place order. */
export function ReviewStep({ order, delivery, next, onOpenMeal, onChange, onBack, onClose }: ReviewStepProps) {
  const session = useOrderSession()
  const place = usePlaceOrder()
  // Placing waits until every change is saved, so the order placed is the one shown.
  const saving = useEditsPending()
  const meals = pickedMeals(order)
  const servings = meals.reduce((total, pick) => total + pick.servings, 0)

  function placeOrder() {
    place.mutate(
      { orderId: order.id, dates: { delivery_date: delivery, next_delivery_date: next }, session },
      { onSuccess: () => haptics.success(), onError: () => haptics.error() },
    )
  }

  return (
    <FlowFrame
      step={stepIndex('review')}
      onBack={onBack}
      onClose={onClose}
      title="Review"
      lede={`Check Order ${order.number}, then place it.`}
      footer={
        <>
          <FooterSummary
            title={`${plural(meals.length, 'meal')} · ${plural(servings, 'serving')}`}
            detail={`Arrives ${formatDate(delivery, 'medium')}`}
          />
          <Button variant="accent" large onClick={placeOrder} disabled={saving || place.isPending}>
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
      <div className={styles.groups}>
        <Group title="Dates">
          <Row
            label="Delivery"
            detail={formatDate(delivery, 'medium')}
            chevron
            onClick={() => onChange('delivery')}
          />
          <Row
            label="Next delivery"
            sub={`Meals cover ${dayRange(coveredDays(delivery, next))}`}
            detail={formatDate(next, 'medium')}
            chevron
            onClick={() => onChange('next')}
          />
        </Group>
        <Group title="Meals">
          {meals.map(({ meal, servings: count }) => (
            <MealRow key={meal.id} meal={meal} servings={count} onOpen={() => onOpenMeal(meal.id, count)} />
          ))}
          <Row label="Change servings" chevron onClick={() => onChange('servings')} />
        </Group>
      </div>
    </FlowFrame>
  )
}
