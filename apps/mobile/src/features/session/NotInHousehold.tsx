import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { errorFields, logger } from '../../lib/log'
import { Button } from '../../ui/Button'
import { Notice } from '../../ui/Notice'
import { signOut, type Session } from './session'
import { Welcome } from './Welcome'

/** A signed-in account in no household: never invited, or removed since. */
export function NotInHousehold({ session }: { session: Session }) {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)

  async function onSignOut() {
    try {
      await signOut(queryClient)
    } catch (e) {
      logger.warn('auth.sign_out_failed', errorFields(e))
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <Welcome
      title="Almost there"
      subtitle={
        <>
          <strong>{session.user.email}</strong> isn't part of a Castle Chef household. Ask someone
          in your household to invite this email, then open the app again.
        </>
      }
    >
      {error && <Notice>Couldn't sign out: {error}</Notice>}
      <Button variant="secondary" block onClick={() => void onSignOut()}>
        Use a different account
      </Button>
    </Welcome>
  )
}
