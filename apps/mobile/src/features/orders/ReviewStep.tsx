import { useCallback, useEffect, useRef } from 'react'
import type { Checkout, ClockTime, GroceryOrder, ISODate } from '../../lib/api/types'
import { addDays, formatClockTime, formatDate } from '../../lib/dates'
import { plural } from '../../lib/format'
import { haptics } from '../../lib/haptics'
import { formatMoney } from '../../lib/products'
import { useNow } from '../../lib/useNow'
import { Button } from '../../ui/Button'
import { CheckoutWait } from '../../ui/CheckoutWait'
import { Chip } from '../../ui/Chip'
import { Group, Row } from '../../ui/Group'
import { Icon } from '../../ui/Icon'
import { Notice } from '../../ui/Notice'
import { OrderCard, OrderCardBody } from '../../ui/OrderCard'
import { Stats } from '../../ui/Stats'
import { MealRow } from '../meals/MealRow'
import { recipeCacheContext } from '../meals/servings'
import { useHousehold } from '../settings/settings'
import { pickedMeals } from './batches'
import { coveredDays, dayRange, deliveryAvailable, deliveryExpired } from './dates'
import { FlowFrame, FooterSummary } from './FlowFrame'
import { listCounts, productTotals, storeName } from './list'
import { useOrderSession } from './lock'
import { useEditsPending, usePlaceOrder, useQuoteOrder } from './orders'
import { quoteState, replacementShown, totalParts } from './quote'
import styles from './ReviewStep.module.css'
import { stepIndex } from './steps'
import { RecipeChanges } from './RecipeChanges'
import { recipeBlockers } from './recipeChangesState'

interface ReviewStepProps {
  order: GroceryOrder
  /** The dates and latest time shown (untouched defaults included), which are placed with it. */
  delivery: ISODate
  by: ClockTime | null
  next: ISODate
  onOpenMeal: (mealId: string, servings?: number) => void
  /** Goes back to a step to change what it chose. */
  onChange: (step: 'servings' | 'delivery' | 'next' | 'list') => void
  onBack: () => void
  onClose: () => void
}

/**
 * The order as it will be placed. It waits while the household's cart is filled
 * with its products and checkout is read (a quote), then shows a card in its
 * color with when it arrives and what's in it, the estimated total, and its
 * meals, delivery, Instacart account, and shopping list, each a way back to
 * change it; then Place order, for that total.
 */
export function ReviewStep({ order, delivery, by, next, onOpenMeal, onChange, onBack, onClose }: ReviewStepProps) {
  const session = useOrderSession()
  const quote = useQuoteOrder()
  const { mutate: startQuote } = quote
  const state = quoteState(order.quote)
  const store = storeName(order)
  const blockers = recipeBlockers(order).map((c) => `${c.job_id}:${c.state}`).sort().join(',')

  // Read a total, once for each one it replaces: when there's none for the order
  // as it is now (the first time here, or it changed since), and when coming
  // back to a total that failed, unless this visit asked for that one itself
  // (it then says it failed, with Try again).
  const replacing = order.quote?.job.id ?? null
  const asked = useRef<string | null | undefined>(undefined)
  const started = useRef(new Set<string>())
  const read = useCallback(() => {
    const jobId = crypto.randomUUID()
    started.current.add(jobId)
    startQuote({ orderId: order.id, jobId, session })
  }, [startQuote, order.id, session])
  useEffect(() => { asked.current = undefined }, [blockers])
  useEffect(() => {
    if (blockers) return
    const again = state === 'failed' && replacing !== null && !started.current.has(replacing)
    if ((state !== 'start' && !again) || asked.current === replacing) return
    asked.current = replacing
    read()
  }, [state, replacing, read, blockers])

  if (blockers) return <FlowFrame step={stepIndex('review')} title="Recipe changes" onBack={onBack} onClose={onClose} footer={<Button large disabled>Place order</Button>}>
    <RecipeChanges order={order} onOpenMeal={onOpenMeal} />
  </FlowFrame>
  const checkout = order.quote?.checkout ?? null
  if ((state === 'ready' || state === 'blocked') && order.quote) {
    return (
      <Reviewed
        order={order}
        checkout={checkout}
        blocked={order.quote.blocked_because}
        quoteId={order.quote.job.id}
        delivery={delivery}
        by={by}
        next={next}
        onOpenMeal={onOpenMeal}
        onChange={onChange}
        onBack={onBack}
        onClose={onClose}
      />
    )
  }
  return (
    <CheckoutWait
      step="review"
      order={order}
      store={store}
      failed={state === 'failed'}
      signedOut={order.quote?.signed_out ?? false}
      startFailed={quote.isError ? quote.error.message : null}
      retrying={quote.isPending}
      onRetry={read}
      onBack={onBack}
      onClose={onClose}
    />
  )
}

interface ReviewedProps extends ReviewStepProps {
  checkout: Checkout | null
  /** Why Instacart won't check the cart out (below the store's minimum); null when it will. */
  blocked: string | null
  quoteId: string
}

/** The order with its total read: everything that will be ordered, and Place order. */
function Reviewed({
  order,
  checkout,
  blocked,
  quoteId,
  delivery,
  by,
  next,
  onOpenMeal,
  onChange,
  onBack,
  onClose,
}: ReviewedProps) {
  const session = useOrderSession()
  const household = useHousehold().shown
  const place = usePlaceOrder()
  const now = useNow(1000)
  // Placing waits until every change is saved, so the order placed is the one shown.
  const saving = useEditsPending()
  const meals = pickedMeals(order)
  const servings = meals.reduce((total, pick) => total + pick.servings, 0)
  const items = order.shopping_list?.items ?? []
  const counts = listCounts(items)
  const products = productTotals(items)
  const store = storeName(order)
  const covered = coveredDays(delivery, next)
  const arrives = by ? `${formatDate(delivery, 'medium')} by ${formatClockTime(by)}` : formatDate(delivery, 'medium')
  const expired = deliveryExpired(delivery, by, order.delivery_time_zone, now)
  const unbookable = by === null || !deliveryAvailable(order, delivery, by, now)
  const unsaved = delivery !== order.delivery_date || by !== order.delivery_by || next !== order.next_delivery_date

  function placeOrder() {
    if (by === null) return
    place.mutate(
      {
        orderId: order.id,
        dates: { delivery_date: delivery, delivery_by: by, next_delivery_date: next },
        quoteId,
        session,
      },
      { onSuccess: () => haptics.success(), onError: () => haptics.error() },
    )
  }

  return (
    <FlowFrame
      step={stepIndex('review')}
      onBack={onBack}
      onClose={onClose}
      notice={
        place.isError ? (
          <Notice>Couldn't place the order: {place.error.message}</Notice>
        ) : expired ? (
          <Notice>Your saved delivery has passed. Choose a new date and time; nothing has been moved.</Notice>
        ) : unbookable || unsaved ? (
          <Notice>Open Delivery to check the available times and confirm your selection.</Notice>
        ) : blocked ? (
          <Notice>
            {store} won't take this order yet ({blocked}). Add to the shopping list to order it.
          </Notice>
        ) : null
      }
      title="Review"
      lede={`Everything look right? Place it, and Castle Chef books it at ${store}.`}
      footer={
        <>
          <FooterSummary
            title={checkout ? `${formatMoney(checkout.total_cents)} estimated` : plural(products.count, 'product')}
            detail={`Arrives ${arrives}`}
          />
          <Button
            variant="accent"
            large
            onClick={placeOrder}
            disabled={recipeBlockers(order).length > 0 || checkout === null || by === null || unbookable || unsaved || saving || place.isPending}
          >
            {place.isPending ? 'Placing…' : 'Place order'}
          </Button>
        </>
      }
    >
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

        {checkout && (
          <Group
            footer={`${
              household
                ? `The tip is ${household.tip_percent}% of the items, up to ${formatMoney(household.tip_max_cents)}. `
                : ''
            }Prices can change before it's booked; you pay what ${store} charges.`}
          >
            <Row
              label={<b>Estimated total</b>}
              sub={totalParts(checkout)}
              detail={<b>{formatMoney(checkout.total_cents)}</b>}
            />
          </Group>
        )}

        <Group title="Meals">
          {meals.map(({ meal, servings: count }) => (
            <MealRow key={meal.id} meal={meal} prefetchContext={recipeCacheContext(order, meal.id, false)} servings={count} onOpen={() => onOpenMeal(meal.id, count)} />
          ))}
          <Row label="Change servings" chevron onClick={() => onChange('servings')} />
        </Group>

        <Group title="Delivery">
          <Row
            label="Arrives"
            sub="A later arrival needs your approval."
            detail={arrives}
            chevron
            onClick={() => onChange('delivery')}
          />
          <Row
            label="Next delivery"
            sub={`Order ${order.number + 1}`}
            detail={formatDate(next, 'medium')}
            chevron
            onClick={() => onChange('next')}
          />
        </Group>

        {checkout && (
          <Group title="Instacart" footer="From your Instacart account; change them there.">
            <Row label="Deliver to" sub={checkout.address} />
            <Row label="Pay with" detail={checkout.card} />
            <Row label="If an item is out of stock" detail={replacementShown(checkout.replacements)} />
          </Group>
        )}

        <Group title="Shopping list">
          <Row
            label={`${plural(products.count, 'product')} at ${store}`}
            sub={counts.removed > 0 ? `${plural(counts.removed, 'item')} not bought` : undefined}
            chevron
            onClick={() => onChange('list')}
          />
        </Group>
      </div>
    </FlowFrame>
  )
}
