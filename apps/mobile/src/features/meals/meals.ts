import { queryOptions } from '@tanstack/react-query'
import { apiRequest } from '../../lib/api/client'
import { isRetryable } from '../../lib/api/errors'
import type { Meal, PlacedMeal } from '../../lib/api/types'
import { queryKeys } from '../../lib/query/keys'

/** The route of a placed order's meal, under a tab: its recipe at the servings ordered. */
export const placedMealPath = (tab: string, meal: PlacedMeal) => `${tab}/meals/${meal.id}?servings=${meal.servings}${meal.prepared_build_id ? `&build=${encodeURIComponent(meal.prepared_build_id)}` : ''}`

/** One meal's full recipe, and the household's rating of it. */
export const mealQuery = (mealId: string, context?: string) =>
  queryOptions({
    queryKey: context === undefined ? queryKeys.meal(mealId) : [...queryKeys.meal(mealId), context],
    queryFn: ({ signal }) => apiRequest<Meal>('GET', `/v1/meals/${mealId}`, undefined, undefined, signal),
    // A 404 (not one of the household's meals) won't change on a retry.
    retry: (failureCount, error) => isRetryable(error, failureCount),
  })
