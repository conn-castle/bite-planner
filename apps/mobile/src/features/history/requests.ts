import {
  type QueryClient,
  queryOptions,
  useMutation,
  useMutationState,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { apiRequest } from '../../lib/api/client'
import { isRetryable } from '../../lib/api/errors'
import type { Job } from '../../lib/api/types'
import { logger } from '../../lib/log'
import { sessionQuery } from '../session/session'

/** Meal requests: the household's jobs, their meals, and asking for more. */

export const MIN_MEALS = 1
export const MAX_MEALS = 14

export const isActive = (job: Job) => job.status === 'pending' || job.status === 'running'

export const jobsQuery = queryOptions({
  queryKey: ['jobs'],
  queryFn: async () => {
    const jobs = await apiRequest<Job[]>('GET', '/v1/jobs')
    logger.debug('jobs.loaded', {
      count: jobs.length,
      active: jobs.filter(isActive).map((job) => ({ id: job.id, status: job.status })),
    })
    return jobs
  },
})

export function useJobs() {
  return useQuery({
    ...jobsQuery,
    // Follow requests that are waiting or cooking; stop once all are done.
    refetchInterval: (query) => (query.state.data?.some(isActive) ? 5_000 : false),
  })
}

export interface MealRequest {
  /** Chosen here, so a retried request (e.g. resumed after going offline) isn't duplicated. */
  jobId: string
  count: number
}

const REQUEST_MEALS = ['requestMeals']
export const OPTIMISTIC_PREFIX = 'optimistic-'

/**
 * How a meal request is sent. Registered at startup rather than in the hook: a
 * request made offline is persisted, and after a relaunch it is restored
 * without the hook's options, yet still needs to know how to send itself.
 */
export function registerRequestMeals(queryClient: QueryClient) {
  queryClient.setMutationDefaults(REQUEST_MEALS, {
    mutationFn: async ({ jobId, count }: MealRequest) => {
      await apiRequest<Job>('POST', '/v1/jobs', { job_id: jobId, count })
    },
    // Resent with the same job id, so a response lost in transit can't create a
    // second request: the API returns the one it already has.
    retry: (failureCount, error) => isRetryable(error, failureCount),
    onSettled: () => queryClient.invalidateQueries({ queryKey: jobsQuery.queryKey }),
  })
}

/** Adds the request to the list immediately, then reconciles with the server. */
export function useRequestMeals() {
  const queryClient = useQueryClient()
  return useMutation<void, Error, MealRequest, { previous?: Job[] }>({
    mutationKey: REQUEST_MEALS,
    onMutate: async ({ jobId, count }) => {
      await queryClient.cancelQueries({ queryKey: jobsQuery.queryKey })
      const previous = queryClient.getQueryData(jobsQuery.queryKey)
      const optimistic: Job = {
        id: `${OPTIMISTIC_PREFIX}${jobId}`,
        kind: 'generate_meals',
        params: { count },
        status: 'pending',
        error: null,
        created_at: new Date().toISOString(),
        finished_at: null,
        meals: [],
      }
      queryClient.setQueryData(jobsQuery.queryKey, (jobs = []) => [optimistic, ...jobs])
      return { previous }
    },
    onError: (_error, _request, context) => {
      queryClient.setQueryData(jobsQuery.queryKey, context?.previous)
      // A refusal can mean the account was removed from the household.
      void queryClient.invalidateQueries({ queryKey: sessionQuery.queryKey })
    },
  })
}

/**
 * Meal requests the API refused, including ones sent after a relaunch (those
 * resume without any component watching them), until the user dismisses them.
 */
export function useFailedMealRequests() {
  const queryClient = useQueryClient()
  const errors = useMutationState({
    filters: { mutationKey: REQUEST_MEALS, status: 'error' },
    select: (mutation) => mutation.state.error,
  })
  function dismiss() {
    const cache = queryClient.getMutationCache()
    for (const mutation of cache.findAll({ mutationKey: REQUEST_MEALS, status: 'error' })) {
      cache.remove(mutation)
    }
  }
  return { errors, dismiss }
}
