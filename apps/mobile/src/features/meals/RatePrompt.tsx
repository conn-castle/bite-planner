import { useState } from 'react'
import { localDate } from '../../lib/dates'
import { MealArt } from '../../ui/art'
import { Notice } from '../../ui/Notice'
import { OrderListCard } from '../../ui/OrderListCard'
import { usePlacedOrders } from '../orders/orders'
import { coverage, coveringFrom } from '../week/home'
import { lastWeeksMealsToRate } from './lastWeek'
import { MealRatingChoices } from './MealRating'
import { useRatingError } from './ratings'
import styles from './RatePrompt.module.css'

/**
 * "How were these meals?": last week's unrated meals, by order, each with the
 * three ratings. The meals are the ones unrated when it first shows, and they
 * stay while it's open, so a rating can be changed or undone.
 */
export function RatePrompt() {
  const today = localDate(new Date())
  // Home's query: its window (from 10 days ago) holds the last week.
  const placed = usePlacedOrders(coveringFrom(today))
  const [asked, setAsked] = useState<ReadonlySet<string>>()

  const error = useRatingError([...(asked ?? [])])

  // Chosen once, from orders just fetched (not a cached copy being refreshed, or
  // one left over from a failed refresh), set during render: React's way to
  // derive state from data.
  if (!asked && placed.isSuccess && !placed.isFetching && !placed.isStale) {
    const toRate = lastWeeksMealsToRate(placed.data, today)
    setAsked(new Set(toRate.flatMap(({ meals }) => meals.map((meal) => meal.id))))
  }

  if (placed.isError && !asked) {
    return <Notice>Couldn't load last week's meals to rate: {placed.error.message}</Notice>
  }
  if (!asked || asked.size === 0 || !placed.data) return null
  const orders = placed.data
    .map((order) => ({ order, meals: order.meals.filter((meal) => asked.has(meal.id)) }))
    .filter(({ meals }) => meals.length > 0)

  return (
    <div className={styles.prompt}>
      {error && <Notice>Couldn't save a rating: {error.message}</Notice>}
      {orders.map(({ order, meals }) => (
        <OrderListCard
          key={order.id}
          number={order.number}
          overline={`Order ${order.number} · ${coverage(order)}`}
          title="How were these meals?"
        >
          {meals.map((meal) => (
            <div key={meal.id} className={styles.row}>
              <span className={styles.art}>
                <MealArt />
              </span>
              <span className={styles.name}>{meal.name}</span>
              <MealRatingChoices meal={meal} />
            </div>
          ))}
        </OrderListCard>
      ))}
    </div>
  )
}
