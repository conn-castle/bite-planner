import type { GroceryOrder, ISODate } from '../../lib/api/types'
import { addDays, formatDate } from '../../lib/dates'
import { haptics } from '../../lib/haptics'
import { Button } from '../../ui/Button'
import { Chip } from '../../ui/Chip'
import { Group, Row } from '../../ui/Group'
import { Notice } from '../../ui/Notice'
import { Icon } from '../../ui/Icon'
import { OrderCard, OrderCardBody } from '../../ui/OrderCard'
import { Stats } from '../../ui/Stats'
import { MealRow } from '../meals/MealRow'
import { useHousehold } from '../settings/settings'
import { pickedMeals } from './batches'
import { coveredDays, dayRange } from './dates'
import { FlowFrame, FooterSummary } from './FlowFrame'
import { plural } from './format'
import { listCounts, productTotals, totalShown } from './list'
import { useOrderSession } from './lock'
import { useEditsPending, usePlaceOrder } from './orders'
import styles from './ReviewStep.module.css'
import { stepIndex } from './steps'

interface ReviewStepProps {
  order: GroceryOrder
  /** The dates shown (untouched defaults included), which are placed with it. */
  delivery: ISODate
  next: ISODate
  onOpenMeal: (mealId: string, servings: number) => void
  /** Goes back to a step to change what it chose. */
  onChange: (step: 'servings' | 'delivery' | 'next' | 'list') => void
  onBack: () => void
  onClose: () => void
}

/**
 * The order as it will be placed: a card in its color with when it arrives and
 * what's in it, then its meals, dates, and shopping list, each a way back to
 * change it; then Place order.
 */
export function ReviewStep({ order, delivery, next, onOpenMeal, onChange, onBack, onClose }: ReviewStepProps) {
  const session = useOrderSession()
  const household = useHousehold().shown
  const place = usePlaceOrder()
  // Placing waits until every change is saved, so the order placed is the one shown.
  const saving = useEditsPending()
  const meals = pickedMeals(order)
  const servings = meals.reduce((total, pick) => total + pick.servings, 0)
  const items = order.shopping_list?.items ?? []
  const counts = listCounts(items)
  const products = productTotals(items)
  const store = order.shopping_list?.store ?? 'the store'
  const covered = coveredDays(delivery, next)

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
      lede="Everything look right? Place it, and the groceries are on their way."
      footer={
        <>
          <FooterSummary
            title={`${plural(products.count, 'product')} · ${plural(meals.length, 'meal')}`}
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
      <div className={styles.wide}>
        <OrderCard
          className={styles.card}
          number={order.number}
          look="placed"
          chip={
            <Chip tone="accent" icon="spark">
              Ready to place
            </Chip>
          }
          title={`Arrives ${formatDate(delivery, 'long')}`}
          subtitle={`Covers ${dayRange(covered)} · ${plural(covered.length, 'day')}`}
        >
          <OrderCardBody>
            <Stats
              className={styles.stats}
              stats={[
                { value: meals.length, label: meals.length === 1 ? 'meal' : 'meals' },
                { value: servings, label: servings === 1 ? 'serving' : 'servings' },
                { value: products.count, label: products.count === 1 ? 'product' : 'products' },
              ]}
            />
            {household && (
              <p className={styles.next}>
                <Icon name="cal" />
                <span>
                  Order {order.number + 1} would need to arrive by <b>{formatDate(next, 'short')}</b> · decide by{' '}
                  {formatDate(addDays(next, -household.decide_by_offset_days), 'short')}
                </span>
              </p>
            )}
          </OrderCardBody>
        </OrderCard>

        <Group title="Meals">
          {meals.map(({ meal, servings: count }) => (
            <MealRow key={meal.id} meal={meal} servings={count} onOpen={() => onOpenMeal(meal.id, count)} />
          ))}
          <Row label="Change servings" chevron onClick={() => onChange('servings')} />
        </Group>

        <Group title="Dates">
          <Row label="Delivery" detail={formatDate(delivery, 'medium')} chevron onClick={() => onChange('delivery')} />
          <Row
            label="Next delivery"
            sub={`Order ${order.number + 1}`}
            detail={formatDate(next, 'medium')}
            chevron
            onClick={() => onChange('next')}
          />
        </Group>

        <Group title="Shopping list">
          <Row
            label={`${plural(products.count, 'product')} at ${store}`}
            sub={`${totalShown(products)} before fees${counts.removed > 0 ? ` · ${plural(counts.removed, 'item')} not bought` : ''}`}
            chevron
            onClick={() => onChange('list')}
          />
        </Group>
      </div>
    </FlowFrame>
  )
}
