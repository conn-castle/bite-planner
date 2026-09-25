import { queryOptions, type QueryClient, useQuery } from '@tanstack/react-query'
import { apiRequest } from '../../lib/api/client'
import type { Household, Me } from '../../lib/api/types'
import { auth, type User } from '../../lib/auth/auth'
import { logger } from '../../lib/log'
import { uploadLogs } from '../../lib/logUpload'
import { persister } from '../../lib/query/queryClient'

/** Who is signed in, and the household they belong to (null: not invited yet). */
export interface Session {
  user: User
  household: Household | null
}

export const sessionQuery = queryOptions({
  queryKey: ['session'],
  queryFn: async (): Promise<Session | null> => {
    const user = await auth.getUser()
    if (!user) return null
    const { household } = await apiRequest<Me>('GET', '/v1/me')
    logger.info('session.loaded', { userId: user.id, householdId: household?.id ?? null })
    return { user, household }
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
  // Upload this account's logs while it still can, so they aren't sent later as
  // whoever signs in next. uploadLogs never throws; what it can't send stays queued.
  await uploadLogs()
  await auth.signOut()
  queryClient.setQueryData(sessionQuery.queryKey, null)
  removeAccountData(queryClient)
  // A request made offline must not be sent later as whoever signs in next.
  queryClient.getMutationCache().clear()
  await persister.removeClient()
  logger.info('auth.signed_out')
}
