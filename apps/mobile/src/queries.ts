import { Capacitor } from '@capacitor/core'
import { Device } from '@capacitor/device'
import {
  queryOptions,
  type QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { getMe, listJobMeals, listJobs, type MealRequest, sendLogs } from './api'
import { auth } from './auth'
import { getLogEntries, logger } from './log'
import { persister } from './queryClient'
import type { Job } from './types'

export interface Session {
  user: { id: string; email: string; name: string }
  member: boolean
}

export const sessionQuery = queryOptions({
  queryKey: ['session'],
  queryFn: async (): Promise<Session | null> => {
    const user = await auth.getUser()
    if (!user) return null
    const session = { user, member: (await getMe()).member }
    logger.info('session.loaded', { userId: user.id, member: session.member })
    return session
  },
  // Always stale: the persisted session renders the app instantly, and is
  // re-checked in the background on every launch and return to the foreground.
  staleTime: 0,
})

export function useSession() {
  return useQuery(sessionQuery)
}

// Drops cached data that belongs to an account. The session entry is updated in
// place instead: `queryClient.clear()` would detach the components watching it.
function removeAccountData(queryClient: QueryClient) {
  queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== 'session' })
}

/** Start clean for the account that just signed in, then load its session. */
export async function afterSignIn(queryClient: QueryClient) {
  removeAccountData(queryClient)
  await queryClient.fetchQuery(sessionQuery)
}

export async function signOut(queryClient: QueryClient) {
  await auth.signOut()
  queryClient.setQueryData(sessionQuery.queryKey, null)
  removeAccountData(queryClient)
  // A request made offline must not be sent later as whoever signs in next.
  queryClient.getMutationCache().clear()
  await persister.removeClient()
  logger.info('auth.signed_out')
}

const isActive = (job: Job) => job.status === 'pending' || job.status === 'running'

export const jobsQuery = queryOptions({
  queryKey: ['jobs'],
  queryFn: async () => {
    const jobs = await listJobs()
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
    refetchInterval: (query) => (query.state.data?.some(isActive) ? 10_000 : false),
  })
}

export const jobMealsQuery = (jobId: string) =>
  queryOptions({
    queryKey: ['meals', jobId],
    queryFn: async () => {
      const meals = await listJobMeals(jobId)
      logger.debug('meals.loaded', { jobId, count: meals.length })
      return meals
    },
    // A finished request's meals never change.
    staleTime: Infinity,
  })

export const OPTIMISTIC_PREFIX = 'optimistic-'

/** Adds the request to the list immediately, then reconciles with the server. */
export function useRequestMeals() {
  const queryClient = useQueryClient()
  // mutationFn and onSettled are defaults set in queryClient.ts.
  return useMutation<void, Error, MealRequest, { previous?: Job[] }>({
    mutationKey: ['requestMeals'],
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

export function useSendLogs() {
  return useMutation({
    mutationKey: ['sendLogs'],
    mutationFn: async () => {
      const info = await Device.getInfo()
      const device = {
        platform: Capacitor.getPlatform(),
        model: info.model,
        os: `${info.operatingSystem} ${info.osVersion}`,
        webView: info.webViewVersion,
        appVersion: __APP_VERSION__,
        appRelease: __APP_RELEASE__,
        userAgent: navigator.userAgent,
      }
      const entries = await getLogEntries()
      await sendLogs(entries, device)
      logger.info('logs.sent', { entries: entries.length })
    },
  })
}
