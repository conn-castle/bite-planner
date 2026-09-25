import { queryOptions } from '@tanstack/react-query'
import { apiRequest } from '../../lib/api/client'
import type { Meal } from '../../lib/api/types'
import { logger } from '../../lib/log'

/** The meals one request produced, with full recipes. */
export const jobMealsQuery = (jobId: string) =>
  queryOptions({
    queryKey: ['meals', jobId],
    queryFn: async () => {
      const meals = await apiRequest<Meal[]>('GET', `/v1/jobs/${jobId}/meals`)
      logger.debug('meals.loaded', { jobId, count: meals.length })
      return meals
    },
    // A finished request's meals never change.
    staleTime: Infinity,
  })
