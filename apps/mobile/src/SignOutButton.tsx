import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { errorFields, logger } from './log'
import { signOut } from './queries'

export function SignOutButton() {
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function onClick() {
    setBusy(true)
    try {
      await signOut(queryClient)
    } catch (e) {
      logger.error('auth.sign_out_failed', errorFields(e))
      setError(e instanceof Error ? e.message : String(e))
      setBusy(false)
    }
  }

  return (
    <>
      {error && <p className="error">Could not sign out: {error}</p>}
      <button className="secondary" onClick={() => void onClick()} disabled={busy}>
        Sign out
      </button>
    </>
  )
}
