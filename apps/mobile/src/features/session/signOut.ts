import type { QueryClient } from '@tanstack/react-query'
import { auth } from '../../lib/auth/auth'
import { logger } from '../../lib/log'
import { uploadLogs } from '../../lib/logUpload'
import { persister } from '../../lib/query/queryClient'
import { forgetThisPhone } from '../notifications/notifications'
import { removeAccountData, sessionQuery } from './session'

export async function signOut(queryClient: QueryClient) {
  // Upload this account's logs while it still can, so they aren't sent later as
  // whoever signs in next. uploadLogs never throws; what it can't send stays queued.
  await uploadLogs()
  // While still signed in: this phone stops getting this account's notifications.
  await forgetThisPhone(queryClient)
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
