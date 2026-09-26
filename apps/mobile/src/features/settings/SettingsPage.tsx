import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { DECIDE_BY_OFFSET_DAYS, NAME_LENGTH, REMINDER_LEAD_DAYS, SERVINGS } from '../../lib/api/limits'
import type { Household, PersonalSettings } from '../../lib/api/types'
import { errorFields, logger } from '../../lib/log'
import { Button } from '../../ui/Button'
import { Group, Row } from '../../ui/Group'
import { Notice } from '../../ui/Notice'
import { Page } from '../../ui/Page'
import { Skeleton } from '../../ui/Skeleton'
import { Stepper } from '../../ui/Stepper'
import { Switch } from '../../ui/Switch'
import { TimeField } from '../../ui/TimeField'
import { useTextPrompt } from '../../ui/useTextPrompt'
import { askPermission, openNotificationSettings, pushAvailable, usePermission } from '../notifications/notifications'
import { plural } from '../orders/format'
import { signOut, useSession } from '../session/session'
import styles from './SettingsPage.module.css'
import {
  useChangeHousehold,
  useChangeName,
  useChangePersonal,
  useHousehold,
  useHouseholdError,
  useNameError,
  usePersonalError,
  usePersonalSettings,
  useUserName,
} from './settings'

export function SettingsPage() {
  const { data: session } = useSession()
  const household = useHousehold()
  const personal = usePersonalSettings()
  const name = session?.household?.name

  return (
    <Page title="Settings" overline={name ? `You · ${name}` : undefined}>
      {household.shown ? (
        <HouseholdGroup household={household.shown} userId={session?.user.id} />
      ) : household.isError ? (
        <Group title="Household">
          <Row label="Couldn't load the household's settings" sub={household.error.message} />
        </Group>
      ) : (
        <Group title="Household">
          <Row label={<Skeleton height={16} width="60%" />} />
        </Group>
      )}
      {personal && <PersonalGroup settings={personal} />}
      <AccountGroup />
    </Page>
  )
}

function HouseholdGroup({ household, userId }: { household: Household; userId: string | undefined }) {
  const change = useChangeHousehold()
  const error = useHouseholdError()
  const prompt = useTextPrompt()
  const { decide_by_offset_days: offset, reminder_lead_days: lead, default_servings: servings } = household
  const members = household.members.map((member) =>
    member.user_id === userId ? 'You' : (member.name ?? member.email ?? 'Someone'),
  )

  return (
    <>
      <Group
        title="Household"
        footerIcon="users"
        footer="Shared by everyone in the household. Changing the decide-by or reminder days moves them for everyone."
      >
        <Row
          label="Name"
          detail={household.name}
          chevron
          onClick={() =>
            prompt({
              title: 'Household name',
              value: household.name,
              maxLength: NAME_LENGTH,
              onSave: (name) => change.mutate({ name }),
            })
          }
        />
        <Row
          label="Decide by"
          sub={`${plural(offset, 'day')} before delivery`}
          detail={
            <Stepper
              inRow
              value={offset}
              min={DECIDE_BY_OFFSET_DAYS.min}
              max={DECIDE_BY_OFFSET_DAYS.max}
              label="Days before delivery"
              onChange={(value) => change.mutate({ decide_by_offset_days: value })}
            />
          }
        />
        <Row
          label="Reminders start"
          sub={lead === 0 ? 'On the decide-by day' : `${plural(lead, 'day')} before decide-by`}
          detail={
            <Stepper
              inRow
              value={lead}
              min={REMINDER_LEAD_DAYS.min}
              max={REMINDER_LEAD_DAYS.max}
              label="Days before decide-by"
              onChange={(value) => change.mutate({ reminder_lead_days: value })}
            />
          }
        />
        <Row
          label="Default servings"
          detail={
            <Stepper
              inRow
              value={servings}
              min={SERVINGS.min}
              max={SERVINGS.max}
              label="Servings"
              onChange={(value) => change.mutate({ default_servings: value })}
            />
          }
        />
        <Row label="Members" detail={members.join(', ')} />
      </Group>
      {error && (
        <div className={styles.notice}>
          <Notice>Couldn't save the change: {error.message}</Notice>
        </div>
      )}
    </>
  )
}

function PersonalGroup({ settings }: { settings: PersonalSettings }) {
  const change = useChangePersonal()
  const error = usePersonalError()

  return (
    <>
      <Group
        title="Personal"
        footerIcon="bell"
        footer="Just for you. Everyone else in the household gets the same reminders at their own time. Meal ideas you ask for always tell you when they're ready."
      >
        {pushAvailable && <AllowNotificationsRow />}
        <Row
          label="Daily reminders"
          sub="To pick meals, until the order is placed"
          detail={
            <Switch
              checked={settings.reminders_on}
              label="Daily reminders"
              onChange={(on) => change.mutate({ reminders_on: on })}
            />
          }
        />
        <Row
          label="Notification time"
          sub="When your daily reminder comes"
          detail={
            <TimeField
              // "08:00:00" from the server; the field shows hours and minutes.
              value={settings.notification_time.slice(0, 5)}
              label="Notification time"
              onChange={(time) => change.mutate({ notification_time: time })}
            />
          }
        />
      </Group>
      {error && (
        <div className={styles.notice}>
          <Notice>Couldn't save the change: {error.message}</Notice>
        </div>
      )}
    </>
  )
}

/**
 * Shown only while iOS doesn't let Castle Chef notify this phone (iOS keeps that
 * setting, not the app): a button to allow it the first time, and to iOS
 * Settings after that, where only the person can turn it on.
 */
function AllowNotificationsRow() {
  const permission = usePermission()
  const [problem, setProblem] = useState<string | null>(null)
  if (permission === null || permission === 'on') return null

  async function open() {
    setProblem(null)
    try {
      if (permission === 'not-set') await askPermission()
      else await openNotificationSettings()
    } catch (error) {
      logger.warn('push.settings_failed', errorFields(error))
      setProblem(error instanceof Error ? error.message : String(error))
    }
  }

  return (
    <Row
      label={permission === 'not-set' ? 'Notifications need your OK' : 'Notifications are off in iOS'}
      sub={
        problem
          ? `Couldn't open it: ${problem}`
          : permission === 'not-set'
            ? 'Allow them to get reminders and meal ideas on this phone'
            : 'Turn them on in iOS Settings to get reminders and meal ideas'
      }
      detail={
        <Button variant="accent" onClick={() => void open()}>
          {permission === 'not-set' ? 'Allow' : 'Open Settings'}
        </Button>
      }
    />
  )
}

function AccountGroup() {
  const queryClient = useQueryClient()
  const { data: session } = useSession()
  const name = useUserName()
  const changeName = useChangeName()
  const nameError = useNameError()
  const prompt = useTextPrompt()
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
    <>
      <Group
        title="Account"
        footer={
          signOutError
            ? `Couldn't sign out: ${signOutError}`
            : nameError
              ? `Couldn't change your name: ${nameError.message}`
              : undefined
        }
      >
        {session && (
          <>
            <Row
              label="Your name"
              detail={name || 'Add'}
              chevron
              onClick={() =>
                prompt({
                  title: 'Your name',
                  value: name ?? '',
                  maxLength: NAME_LENGTH,
                  onSave: (text) => changeName.mutate({ name: text }),
                })
              }
            />
            <Row label="Email" detail={session.user.email} />
          </>
        )}
        <Row label="Sign out" tone="danger" onClick={() => void onSignOut()} disabled={signingOut} />
      </Group>
      <p className={styles.version}>
        Castle Chef {__APP_VERSION__} · {__APP_RELEASE__}
      </p>
    </>
  )
}
