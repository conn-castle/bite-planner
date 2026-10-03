import { describe, expect, it } from 'vitest'
import type { MealRating, MealSummary, PlacedOrder } from '../../lib/api/types'
import { historyOrders as filtered } from './history'

const historyOrders = (orders: PlacedOrder[], search: string, filter: Parameters<typeof filtered>[2]) =>
  filtered(orders, search, filter, (meal) => meal.rating)

const meal = (name: string, rating: MealRating | null = null): MealSummary => ({
  id: name,
  name,
  description: null,
  active_time_minutes: null,
  total_time_minutes: null,
  rating,
})

const order = (number: number, delivery: string, meals: MealSummary[]): PlacedOrder => ({
  book_state: 'booked',
  book_on: '2026-09-20',
  booking: null,
  book_issue: null,
  id: `order-${number}`,
  number,
  placed_at: `${delivery}T09:00:00Z`,
  placed_by: { user_id: 'sam', name: 'Sam' },
  delivery_date: delivery,
  delivery_by: null,
  next_delivery_date: delivery,
  shopping_list: [],
  meals: meals.map((meal) => ({ ...meal, servings: 4 })),
})

const names = (result: ReturnType<typeof filtered>) =>
  result.map(({ order, meals }) => [order.number, meals.map((each) => each.name)])

const orders = [
  order(1, '2026-09-06', [meal('Beef stir-fry', 'down'), meal('Chicken fajitas', 'favorite')]),
  order(2, '2026-09-13', [meal('Crème brûlée', 'up'), meal('Salmon tacos')]),
  order(3, '2026-09-20', [meal('Chicken curry', 'up'), meal('Liver', 'not_for_us')]),
]

describe('historyOrders', () => {
  it('lists every order newest first, with all its meals', () => {
    expect(names(historyOrders(orders, '', 'all'))).toEqual([
      [3, ['Chicken curry', 'Liver']],
      [2, ['Crème brûlée', 'Salmon tacos']],
      [1, ['Beef stir-fry', 'Chicken fajitas']],
    ])
  })

  it('searches any part of the name, ignoring case and accents, and leaves out orders without a match', () => {
    expect(names(historyOrders(orders, 'CHICK', 'all'))).toEqual([
      [3, ['Chicken curry']],
      [1, ['Chicken fajitas']],
    ])
    expect(names(historyOrders(orders, 'creme brulee', 'all'))).toEqual([[2, ['Crème brûlée']]])
    expect(names(historyOrders(orders, '  fajitas chicken ', 'all'))).toEqual([[1, ['Chicken fajitas']]])
  })

  it('counts a favorite as liked', () => {
    expect(names(historyOrders(orders, '', 'liked'))).toEqual([
      [3, ['Chicken curry']],
      [2, ['Crème brûlée']],
      [1, ['Chicken fajitas']],
    ])
    expect(names(historyOrders(orders, '', 'favorite'))).toEqual([[1, ['Chicken fajitas']]])
  })

  it('filters the disliked and the unrated', () => {
    // "Not for us" on a meal ordered anyway is not liked.
    expect(names(historyOrders(orders, '', 'disliked'))).toEqual([
      [3, ['Liver']],
      [1, ['Beef stir-fry']],
    ])
    expect(names(historyOrders(orders, '', 'unrated'))).toEqual([[2, ['Salmon tacos']]])
  })

  it('filters by the rating a meal shows, e.g. one still being saved', () => {
    const favoriteSoon = (meal: MealSummary) => (meal.name === 'Salmon tacos' ? 'favorite' : meal.rating)
    expect(names(filtered(orders, '', 'favorite', favoriteSoon))).toEqual([
      [2, ['Salmon tacos']],
      [1, ['Chicken fajitas']],
    ])
    expect(names(filtered(orders, '', 'unrated', favoriteSoon))).toEqual([])
  })

  it('combines the search and the filter', () => {
    expect(names(historyOrders(orders, 'chicken', 'favorite'))).toEqual([[1, ['Chicken fajitas']]])
    expect(historyOrders(orders, 'salmon', 'liked')).toEqual([])
  })
})
