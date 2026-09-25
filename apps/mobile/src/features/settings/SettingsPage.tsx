import { useQueryClient } from '@tanstack/react-query'
import { home, logOutOutline } from 'ionicons/icons'
import { useState } from 'react'
import { errorFields, logger } from '../../lib/log'
import { Group, Row } from '../../ui/Group'
import { Page } from '../../ui/Page'
import { signOut, useSession } from '../session/session'
import styles from './SettingsPage.module.css'

function initials(name: string, email: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  const letters = words.length ? words.slice(0, 2).map((word) => word[0]) : [email[0]]
  return letters.join('').toUpperCase()
}

export function SettingsPage() {
  const queryClient = useQueryClient()
  const { data: session } = useSession()
  const [signingOut, setSigningOut] = useState(false)
  const [signOutError, setSignOutError] = useState<string | null>(null)

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
    <Page title="Settings" largeTitle>
      {session && (
        <div className={styles.profile}>
          <span className={styles.avatar} aria-hidden="true">
            {initials(session.user.name, session.user.email)}
          </span>
          <div className={styles.who}>
            <span className={styles.name}>{session.user.name || session.user.email}</span>
            <span className={styles.email}>{session.user.email}</span>
          </div>
        </div>
      )}

      <Group title="Household">
        <Row icon={home} label={session?.household?.name} />
      </Group>

      <Group footer={signOutError ? `Couldn't sign out: ${signOutError}` : undefined}>
        <Row
          icon={logOutOutline}
          label="Sign out"
          tone="danger"
          onClick={() => void onSignOut()}
          disabled={signingOut}
        />
      </Group>

      <p className={styles.version}>
        Castle Chef {__APP_VERSION__} · {__APP_RELEASE__}
      </p>
    </Page>
  )
}
