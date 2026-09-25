import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { errorFields, logger } from '../../lib/log'
import { Group, Row } from '../../ui/Group'
import { Page } from '../../ui/Page'
import { signOut, useSession } from '../session/session'
import styles from './SettingsPage.module.css'

export function SettingsPage() {
  const queryClient = useQueryClient()
  const { data: session } = useSession()
  const [signingOut, setSigningOut] = useState(false)
  const [signOutError, setSignOutError] = useState<string | null>(null)
  const household = session?.household?.name

  async function onSignOut() {
    setSigningOut(true)
    setSignOutError(null)
    try {
      await signOut(queryClient)
    } catch (error) {
      logger.warn('auth.sign_out_failed', errorFields(error))
      setSignOutError(error instanceof Error ? error.message : String(error))
      setSigningOut(false)
    }
  }

  return (
    <Page title="Settings" overline={household ? `You · ${household}` : undefined}>
      {household && (
        <Group title="Household">
          <Row label={household} />
        </Group>
      )}

      <Group title="Account" footer={signOutError ? `Couldn't sign out: ${signOutError}` : undefined}>
        {session && (
          <Row
            label={session.user.name || session.user.email}
            sub={session.user.name ? session.user.email : undefined}
          />
        )}
        <Row label="Sign out" tone="danger" onClick={() => void onSignOut()} disabled={signingOut} />
      </Group>

      <p className={styles.version}>
        Castle Chef {__APP_VERSION__} · {__APP_RELEASE__}
      </p>
    </Page>
  )
}
