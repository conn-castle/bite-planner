import { queryOptions, type QueryClient, useQuery } from '@tanstack/react-query'
import { apiRequest, phoneTimeZone } from '../../lib/api/client'
import type { HouseholdRef, Me, PersonalSettings } from '../../lib/api/types'
import { auth, type User } from '../../lib/auth/auth'
import { errorFields, logger } from '../../lib/log'
import { uploadLogs } from '../../lib/logUpload'
import { persister } from '../../lib/query/queryClient'

/** Who is signed in, and the household they belong to (null: not invited yet). */
export interface Session {
  user: User
  household: HouseholdRef | null
  /** Their own settings; null when not in a household. */
  settings: PersonalSettings | null
}

/**
 * Records the phone's time zone with the member's settings when it changed
 * (their reminders go out at their notification time in it). Runs with every
 * session fetch, i.e. at launch and on every return to the foreground, so it
 * follows the phone even when the app is never closed. Failing is logged, not
 * shown: the next fetch tries again.
 */
async function withPhoneTimeZone(settings: PersonalSettings): Promise<PersonalSettings> {
  const timeZone = phoneTimeZone()
  if (settings.time_zone === timeZone) return settings
  try {
    const updated = await apiRequest<PersonalSettings>('PATCH', '/v1/me/settings', { time_zone: timeZone })
    logger.info('settings.time_zone_updated', { from: settings.time_zone, to: timeZone })
    return updated
  } catch (error) {
    logger.warn('settings.time_zone_update_failed', errorFields(error))
    return settings
  }
}

export const sessionQuery = queryOptions({
  queryKey: ['session'],
  queryFn: async (): Promise<Session | null> => {
    const user = await auth.getUser()
    if (!user) return null
    const me = await apiRequest<Me>('GET', '/v1/me')
    logger.info('session.loaded', { userId: user.id, householdId: me.household?.id ?? null })
    return { user, household: me.household, settings: me.settings && (await withPhoneTimeZone(me.settings)) }
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
  // A fetch on its way would put the signed-out session back when it lands.
  await queryClient.cancelQueries({ queryKey: sessionQuery.queryKey })
  queryClient.setQueryData(sessionQuery.queryKey, null)
  removeAccountData(queryClient)
  // A request made offline must not be sent later as whoever signs in next.
  queryClient.getMutationCache().clear()
  await persister.removeClient()
  logger.info('auth.signed_out')
}
