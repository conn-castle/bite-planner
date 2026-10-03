import { describe, expect, it } from 'vitest'
import type { MealRating, MealSummary, PlacedOrder } from '../../lib/api/types'
import { lastWeeksMealsToRate } from './lastWeek'

const meal = (id: string, rating: MealRating | null = null): MealSummary => ({
  id,
  name: id,
  description: null,
  active_time_minutes: null,
  total_time_minutes: null,
  rating,
})

const order = (number: number, delivery: string, next: string, meals: MealSummary[]): PlacedOrder => ({
  book_state: 'booked',
  book_on: '2026-09-20',
  booking: null,
  book_issue: null,
  id: `order-${number}`,
  number,
  placed_at: '2026-09-01T09:00:00Z',
  placed_by: { user_id: 'sam', name: 'Sam' },
  delivery_date: delivery,
  delivery_by: null,
  next_delivery_date: next,
  shopping_list: [],
  meals: meals.map((meal) => ({ ...meal, servings: 4 })),
})

const TODAY = '2026-09-24'
const ids = (orders: PlacedOrder[]) =>
  lastWeeksMealsToRate(orders, TODAY).map(({ order, meals }) => [order.number, meals.map((each) => each.id)])

describe('lastWeeksMealsToRate', () => {
  it("asks about the unrated meals of orders whose days reach into the last 7 days", () => {
    expect(
      ids([
        // Covers Sep 10–16: ended before the last 7 days (Sep 17–23).
        order(1, '2026-09-10', '2026-09-17', [meal('old')]),
        // Covers Sep 17–21: in the last week.
        order(2, '2026-09-17', '2026-09-22', [meal('liked', 'up'), meal('a'), meal('b')]),
        // Delivered Sep 22, still covering today.
        order(3, '2026-09-22', '2026-09-27', [meal('c'), meal('gone', 'not_for_us')]),
      ]),
    ).toEqual([
      [2, ['a', 'b']],
      [3, ['c']],
    ])
  })

  it("doesn't ask about orders not delivered yet, or ones fully rated", () => {
    expect(
      ids([
        order(2, '2026-09-17', '2026-09-22', [meal('liked', 'favorite'), meal('disliked', 'down')]),
        order(3, '2026-09-24', '2026-09-29', [meal('today')]),
        order(4, '2026-09-29', '2026-10-03', [meal('later')]),
      ]),
    ).toEqual([])
  })
})
