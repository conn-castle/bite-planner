import { placedMealPath } from '../meals/meals'
import { useIonRouter } from '@ionic/react'
import { useState } from 'react'
import type { MealSummary, PlacedMeal, PlacedOrder } from '../../lib/api/types'
import { addDays, formatDate } from '../../lib/dates'
import { plural } from '../../lib/format'
import { usePressIntent } from '../../lib/usePressIntent'
import { MealArt } from '../../ui/art'
import { CardStack } from '../../ui/CardStack'
import { Pill } from '../../ui/Chip'
import { ChoiceChips } from '../../ui/ChoiceChips'
import { Notice } from '../../ui/Notice'
import { OrderListCard } from '../../ui/OrderListCard'
import { orderColors } from '../../ui/orderColor'
import { Page } from '../../ui/Page'
import { SearchField } from '../../ui/SearchField'
import { Segmented } from '../../ui/Segmented'
import { Skeleton } from '../../ui/Skeleton'
import { usePrefetchMeal } from '../meals/cached'
import { formatMinutes, mealMinutes } from '../meals/facts'
import { MealRatingMark } from '../meals/MealRating'
import { MealRow } from '../meals/MealRow'
import { shownRating, usePendingRatings } from '../meals/ratings'
import { usePlacedOrders } from '../orders/orders'
import { coverage } from '../week/home'
import { HISTORY_FILTERS, type HistoryFilter, type HistoryOrder, historyOrders } from './history'
import styles from './HistoryPage.module.css'

type View = 'orders' | 'meals'

const VIEWS: readonly { value: View; label: string }[] = [
  { value: 'orders', label: 'By order' },
  { value: 'meals', label: 'All meals' },
]

/**
 * History: every placed order and its meals, newest first, by order or as a
 * grid of all the meals, searched by name and filtered by rating.
 */
export function HistoryPage() {
  const placed = usePlacedOrders()
  const router = useIonRouter()
  const [view, setView] = useState<View>('orders')
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<HistoryFilter>('all')
  const orders = placed.data ?? []
  const pending = usePendingRatings()
  const shown = historyOrders(orders, search, filter, (meal) => shownRating(meal, pending))
  const mealCount = orders.reduce((total, order) => total + order.meals.length, 0)
  const openMeal = (meal: PlacedMeal) => router.push(placedMealPath('/history', meal))

  return (
    <Page
      title="History"
      overline={placed.data ? `${plural(orders.length, 'order')} · ${plural(mealCount, 'meal')}` : undefined}
      onRefresh={() => placed.refetch()}
    >
      {placed.isError && (
        <Notice action={<button onClick={() => void placed.refetch()}>Try again</button>}>
          Couldn't load your orders: {placed.error.message}
        </Notice>
      )}
      {placed.isPending && !placed.isError && (
        <div className={styles.loading}>
          <Skeleton height={36} radius={11} />
          <Skeleton height={260} radius={22} />
        </div>
      )}
      {placed.data && orders.length === 0 && (
        <p className={styles.empty}>Orders you place show up here, with their meals grouped by order.</p>
      )}
      {orders.length > 0 && (
        <>
          <Segmented options={VIEWS} value={view} onChange={setView} label="Show" className={styles.views} />
          <SearchField value={search} onChange={setSearch} placeholder="Search meals" className={styles.search} />
          <ChoiceChips
            options={HISTORY_FILTERS}
            value={filter}
            onChange={setFilter}
            label="Filter"
            className={styles.filters}
          />
          {shown.length === 0 && <p className={styles.empty}>No meals match.</p>}
          {view === 'orders' ? (
            <ByOrder
              shown={shown}
              onOpenOrder={(order) => router.push(`/history/orders/${order.id}`)}
              onOpenMeal={openMeal}
            />
          ) : (
            <AllMeals shown={shown} onOpenMeal={openMeal} />
          )}
        </>
      )}
    </Page>
  )
}

interface ByOrderProps {
  shown: HistoryOrder[]
  onOpenOrder: (order: PlacedOrder) => void
  onOpenMeal: (meal: PlacedMeal) => void
}

/** Each order as a card of its meals (spec/mockup.html: .hcard). */
function ByOrder({ shown, onOpenOrder, onOpenMeal }: ByOrderProps) {
  return (
    <CardStack className={styles.cards}>
      {shown.map(({ order, meals }) => (
        <OrderListCard
          key={order.id}
          number={order.number}
          overline={`Order ${order.number} · ${formatDate(order.delivery_date, 'monthDay')}`}
          title={coverage(order)}
          end={<Pill tone="success">Order placed</Pill>}
          onOpen={() => onOpenOrder(order)}
        >
          {meals.map((meal) => (
            <MealRow key={meal.id} meal={meal} onOpen={() => onOpenMeal(meal)} rated />
          ))}
        </OrderListCard>
      ))}
    </CardStack>
  )
}

interface AllMealsProps {
  shown: HistoryOrder[]
  onOpenMeal: (meal: PlacedMeal) => void
}

/** Every meal as a tile, grouped by order (spec/mockup.html: .hgroup, .grid2, .gtile). */
function AllMeals({ shown, onOpenMeal }: AllMealsProps) {
  return (
    <div className={styles.groups}>
      {shown.map(({ order, meals }) => (
        <section key={order.id} className={styles.group} style={orderColors(order.number)}>
          <h2 className={styles.groupHeader}>
            <span>Order {order.number}</span>
            <span>
              {formatDate(order.delivery_date, 'monthDay')} –{' '}
              {formatDate(addDays(order.next_delivery_date, -1), 'monthDay')}
            </span>
          </h2>
          <div className={styles.grid}>
            {meals.map((meal) => (
              <MealTile key={meal.id} meal={meal} onOpen={() => onOpenMeal(meal)} />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

function MealTile({ meal, onOpen }: { meal: MealSummary; onOpen: () => void }) {
  const prefetch = usePrefetchMeal()
  const press = usePressIntent(() => prefetch(meal.id))
  const minutes = mealMinutes(meal)
  return (
    <button type="button" className={styles.tile} onClick={onOpen} {...press}>
      <span className={styles.tileArt}>
        <MealArt meal={meal} />
      </span>
      <span className={styles.tileText}>
        <span className={styles.tileName}>{meal.name}</span>
        <span className={styles.tileFoot}>
          {minutes != null && <small>{formatMinutes(minutes)}</small>}
          <MealRatingMark meal={meal} />
        </span>
      </span>
    </button>
  )
}
