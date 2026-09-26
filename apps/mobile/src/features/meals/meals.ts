import { queryOptions } from '@tanstack/react-query'
import { apiRequest } from '../../lib/api/client'
import { isRetryable } from '../../lib/api/errors'
import type { Meal } from '../../lib/api/types'
import { queryKeys } from '../../lib/query/keys'

/** One meal's full recipe, and the household's rating of it. */
export const mealQuery = (mealId: string) =>
  queryOptions({
    queryKey: queryKeys.meal(mealId),
    queryFn: () => apiRequest<Meal>('GET', `/v1/meals/${mealId}`),
    // A 404 (not one of the household's meals) won't change on a retry.
    retry: (failureCount, error) => isRetryable(error, failureCount),
  })
