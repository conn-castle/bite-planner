import type { ISODate, MealSummary, PlacedOrder } from '../../lib/api/types'
import { addDays } from '../../lib/dates'

// How far back "last week's meals" reach.
const LAST_WEEK_DAYS = 7

export interface OrderToRate {
  order: PlacedOrder
  meals: MealSummary[]
}

/**
 * Last week's meals that aren't rated yet, by order: the meals of orders
 * delivered before today whose days reach into the last 7 days.
 */
export function lastWeeksMealsToRate(orders: readonly PlacedOrder[], today: ISODate): OrderToRate[] {
  const weekAgo = addDays(today, -LAST_WEEK_DAYS)
  return orders
    .filter((order) => order.delivery_date < today && order.next_delivery_date > weekAgo)
    .map((order) => ({ order, meals: order.meals.filter((meal) => meal.rating === null) }))
    .filter(({ meals }) => meals.length > 0)
}
