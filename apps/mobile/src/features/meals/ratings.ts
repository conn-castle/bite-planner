import { type QueryClient, useMutation, useMutationState } from '@tanstack/react-query'
import { apiRequest } from '../../lib/api/client'
import { isRetryable } from '../../lib/api/errors'
import type { Meal, MealRating, MealSummary, PlacedOrder, RatingIn } from '../../lib/api/types'
import { isRecord } from '../../lib/isRecord'
import { queryKeys } from '../../lib/query/keys'

/**
 * The household's ratings of the meals of its placed orders. A rating shows at
 * once: every meal on screen shows the newest one still being saved over what
 * the server sent (`useRating`). Once saved, it is written into every cached
 * copy of the meal (`storeMealRating`). ("Not for us" on a suggestion is an
 * order edit instead, since it also unpicks the meal; see orders.ts, which
 * stores it the same way.)
 */

const RATE = ['meals', 'rate']

// Ratings are saved one at a time, in the order they were made, so the last
// tap on a meal is the one the server keeps.
const RATINGS = { id: 'meal-ratings' }

export interface RateMeal {
  mealId: string
  /** Null clears it (not rated). */
  rating: MealRating | null
}

const RATING_VALUES: readonly MealRating[] = ['not_for_us', 'down', 'up', 'favorite']

/** Whether a mutation's variables are a rating (they are stored untyped). */
export function isRateMeal(value: unknown): value is RateMeal {
  if (!isRecord(value) || typeof value.mealId !== 'string') return false
  const { rating } = value
  return rating === null || RATING_VALUES.some((each) => each === rating)
}

/**
 * Writes a saved rating into the cached meal and placed orders (a draft's copy
 * comes with the draft; see orders.ts). Fetches already on their way are
 * stopped first: they may have been read before the rating was saved, and
 * would overwrite it when they arrive.
 */
export async function storeMealRating(queryClient: QueryClient, { mealId, rating }: RateMeal) {
  const mealKey = queryKeys.meal(mealId)
  await Promise.all([
    queryClient.cancelQueries({ queryKey: mealKey }),
    queryClient.cancelQueries({ queryKey: queryKeys.placedOrders }),
  ])
  queryClient.setQueryData<Meal>(mealKey, (meal) => meal && { ...meal, rating })
  queryClient.setQueriesData<PlacedOrder[]>({ queryKey: queryKeys.placedOrders }, (orders) =>
    orders?.map((order) => ({
      ...order,
      meals: order.meals.map((meal) => (meal.id === mealId ? { ...meal, rating } : meal)),
    })),
  )
}

/** How a rating is sent and stored; registered at startup, like the order requests (see orders.ts). */
export function registerRatingMutations(queryClient: QueryClient) {
  queryClient.setMutationDefaults(RATE, {
    mutationFn: ({ mealId, rating }: RateMeal) =>
      rating
        ? apiRequest<void>('PUT', `/v1/meals/${mealId}/rating`, { rating } satisfies RatingIn)
        : apiRequest<void>('DELETE', `/v1/meals/${mealId}/rating`),
    retry: (failureCount: number, error: Error) => isRetryable(error, failureCount),
    scope: RATINGS,
    onSuccess: (_result: void, change: RateMeal) => storeMealRating(queryClient, change),
    // Refused: show the meal as the server has it (the page says why; see useRatingError).
    onError: (_error: Error, { mealId }: RateMeal) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.meal(mealId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.placedOrders }),
      ]),
  })
}

export function useRateMeal() {
  return useMutation<void, Error, RateMeal>({ mutationKey: RATE })
}

/** The ratings still being saved, by meal: the newest for each. */
export function usePendingRatings(): ReadonlyMap<string, MealRating | null> {
  const pending = useMutationState({
    filters: { mutationKey: RATE, status: 'pending' },
    select: (mutation) => mutation.state.variables,
  }).filter(isRateMeal)
  return new Map(pending.map((change) => [change.mealId, change.rating]))
}

/** A meal's rating as it will be once every rating still being saved is. */
export function shownRating(meal: MealSummary, pending: ReadonlyMap<string, MealRating | null>): MealRating | null {
  const change = pending.get(meal.id)
  return change === undefined ? meal.rating : change
}

/** The meal's rating as it will be once every rating still being saved is: the newest one, or the server's. */
export function useRating(meal: MealSummary): MealRating | null {
  return shownRating(meal, usePendingRatings())
}

/** Why the latest rating of any of these meals couldn't be saved, if it couldn't. */
export function useRatingError(mealIds: readonly string[]): Error | null {
  const latest = new Map<string, Error | null>()
  for (const { variables, error } of useMutationState({
    filters: { mutationKey: RATE },
    select: (mutation) => ({ variables: mutation.state.variables, error: mutation.state.error }),
  })) {
    if (isRateMeal(variables)) latest.set(variables.mealId, error)
  }
  return mealIds.map((id) => latest.get(id) ?? null).find((error) => error !== null) ?? null
}
