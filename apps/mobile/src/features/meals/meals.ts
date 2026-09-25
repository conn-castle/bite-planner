import { queryOptions } from '@tanstack/react-query'
import { apiRequest } from '../../lib/api/client'
import { isRetryable } from '../../lib/api/errors'
import type { Meal } from '../../lib/api/types'

/** One meal's full recipe. */
export const mealQuery = (mealId: string) =>
  queryOptions({
    queryKey: ['meal', mealId],
    queryFn: () => apiRequest<Meal>('GET', `/v1/meals/${mealId}`),
    // A recipe never changes.
    staleTime: Infinity,
    // A 404 (not one of the household's meals) won't change on a retry.
    retry: (failureCount, error) => isRetryable(error, failureCount),
  })
