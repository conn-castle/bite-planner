import type { MealRating, MealSummary, PlacedOrder } from '../../lib/api/types'

/** History's filters: every meal, or those with one kind of rating. */
export type HistoryFilter = 'all' | 'favorite' | 'liked' | 'disliked' | 'unrated'

export const HISTORY_FILTERS: readonly { value: HistoryFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'favorite', label: 'Favorites' },
  { value: 'liked', label: 'Liked' },
  { value: 'disliked', label: "Didn't like" },
  { value: 'unrated', label: 'Not rated' },
]

// A favorite is always liked, and "not for us" (a suggestion's ✕, on a meal ordered anyway) is not liked.
const MATCHES: Record<HistoryFilter, (rating: MealRating | null) => boolean> = {
  all: () => true,
  favorite: (rating) => rating === 'favorite',
  liked: (rating) => rating === 'up' || rating === 'favorite',
  disliked: (rating) => rating === 'down' || rating === 'not_for_us',
  unrated: (rating) => rating === null,
}

export interface HistoryOrder {
  order: PlacedOrder
  /** Its meals that match the search and the filter. */
  meals: MealSummary[]
}

/**
 * The placed orders newest first, each with its meals that match the search
 * (any part of the name, ignoring case and accents) and the filter, by the
 * rating each meal shows (`ratingOf`). Orders with no matching meals are left out.
 */
export function historyOrders(
  orders: readonly PlacedOrder[],
  search: string,
  filter: HistoryFilter,
  ratingOf: (meal: MealSummary) => MealRating | null,
): HistoryOrder[] {
  const words = fold(search).split(/\s+/).filter(Boolean)
  return [...orders]
    .sort((a, b) => b.delivery_date.localeCompare(a.delivery_date) || b.number - a.number)
    .map((order) => ({
      order,
      meals: order.meals.filter(
        (meal) => MATCHES[filter](ratingOf(meal)) && words.every((word) => fold(meal.name).includes(word)),
      ),
    }))
    .filter(({ meals }) => meals.length > 0)
}

/** Lowercase, without accents: "Crème brûlée" matches "creme brulee". */
const fold = (text: string) =>
  text
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLocaleLowerCase()
