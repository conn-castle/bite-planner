import { useIonAlert } from '@ionic/react'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  DECIDE_BY_OFFSET_DAYS,
  EMAIL_LENGTH,
  NAME_LENGTH,
  REMINDER_LEAD_DAYS,
  SERVINGS,
  TIP_MAX_CENTS,
  TIP_PERCENT,
} from '../../lib/api/limits'
import type { Household, HouseholdInvite, HouseholdMember, PersonalSettings, ProductGone } from '../../lib/api/types'
import { errorMessage } from '../../lib/errorMessage'
import { plural } from '../../lib/format'
import { errorFields, logger } from '../../lib/log'
import { Button } from '../../ui/Button'
import { Group, Row } from '../../ui/Group'
import { Icon } from '../../ui/Icon'
import { formatMoney } from '../../lib/products'
import { Notice } from '../../ui/Notice'
import { Page } from '../../ui/Page'
import { Skeleton } from '../../ui/Skeleton'
import { Stepper } from '../../ui/Stepper'
import { Switch } from '../../ui/Switch'
import { TimeField } from '../../ui/TimeField'
import { useTextPrompt } from '../../ui/useTextPrompt'
import { askPermission, openNotificationSettings, pushAvailable, usePermission } from '../notifications/notifications'
import { useSession } from '../session/session'
import { signOut } from '../session/signOut'
import styles from './SettingsPage.module.css'
import {
  useChangeHousehold,
  useChangeName,
  useCancelInvite,
  useChangePersonal,
  useHousehold,
  useHouseholdError,
  useNameError,
  usePersonalError,
  usePersonalSettings,
  useInvite,
  useRemoveMember,
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
        <>
          <HouseholdGroup household={household.shown} />
          <MembersGroup household={household.shown} userId={session?.user.id} />
        </>
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

function HouseholdGroup({ household }: { household: Household }) {
  const change = useChangeHousehold()
  const error = useHouseholdError()
  const prompt = useTextPrompt()
  const { decide_by_offset_days: offset, reminder_lead_days: lead, default_servings: servings } = household

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
              fields: [{ value: household.name, maxLength: NAME_LENGTH }],
              onSave: ([name]) => change.mutate({ name }),
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
      </Group>
      <Group
        title="Delivery"
        footerIcon="truck"
        footer="Each order arrives by the delivery time unless you choose another. The tip is added when it's booked at Instacart."
      >
        <Row
          label="Delivery time"
          sub="The latest it should arrive"
          detail={
            <TimeField
              value={household.delivery_time}
              label="Delivery time"
              onChange={(time) => change.mutate({ delivery_time: time })}
            />
          }
        />
        <Row
          label="Tip"
          sub="Of the items' total"
          detail={
            <Stepper
              inRow
              value={household.tip_percent}
              min={TIP_PERCENT.min}
              max={TIP_PERCENT.max}
              format={(value) => `${value}%`}
              label="Tip percentage"
              onChange={(value) => change.mutate({ tip_percent: value })}
            />
          }
        />
        <Row
          label="Maximum tip"
          sub="Never more, whatever the percentage"
          detail={
            <Stepper
              inRow
              value={household.tip_max_cents}
              min={TIP_MAX_CENTS.min}
              max={TIP_MAX_CENTS.max}
              step={100}
              format={(value) => formatMoney(value).replace(/\.00$/, '')}
              label="Maximum tip"
              onChange={(value) => change.mutate({ tip_max_cents: value })}
            />
          }
        />
      </Group>
      <Group
        title="If a product is gone"
        footer="When an order is booked and one of its products is no longer available. With no alternative left, it asks either way."
      >
        {PRODUCT_GONE.map((option) => (
          <Row
            key={option.value}
            label={option.label}
            sub={option.sub}
            detail={
              household.product_gone === option.value ? (
                <span role="img" aria-label="Chosen">
                  <Icon name="check" />
                </span>
              ) : undefined
            }
            onClick={() => change.mutate({ product_gone: option.value })}
          />
        ))}
      </Group>
      {error && (
        <div className={styles.notice}>
          <Notice>Couldn't save the change: {error.message}</Notice>
        </div>
      )}
    </>
  )
}

const PRODUCT_GONE: readonly { value: ProductGone; label: string; sub: string }[] = [
  { value: 'use_alternative', label: 'Use an alternative', sub: 'The first of its alternatives available' },
  { value: 'ask', label: 'Ask me', sub: 'Nothing is ordered until you choose' },
]

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
              value={settings.notification_time}
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
      setProblem(errorMessage(error))
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

/**
 * Who's in the household, and who's invited: anyone in it invites someone by
 * email (they join on their first sign-in with it), withdraws an invitation, or
 * removes someone, themselves too, but never the last member.
 */
function MembersGroup({ household, userId }: { household: Household; userId: string | undefined }) {
  const [presentAlert] = useIonAlert()
  const prompt = useTextPrompt()
  const invite = useInvite()
  const cancelInvite = useCancelInvite()
  const removeMember = useRemoveMember()
  const failed = invite.error ?? cancelInvite.error ?? removeMember.error
  const alone = household.members.length === 1

  function inviteSomeone() {
    prompt({
      title: 'Invite someone',
      message: 'They join when they first sign in to Castle Chef with this email.',
      fields: [
        { placeholder: 'Name', maxLength: NAME_LENGTH },
        { placeholder: 'Email', maxLength: EMAIL_LENGTH, email: true },
      ],
      action: 'Invite',
      onSave: ([name, email]) => invite.mutate({ name, email }),
    })
  }

  function confirmRemove(member: HouseholdMember) {
    const self = member.user_id === userId
    const who = member.name || member.email || 'them'
    void presentAlert({
      header: self ? `Leave ${household.name}?` : `Remove ${who}?`,
      message: self
        ? "You won't see its orders or meals any more. Someone can invite you back."
        : `They won't see ${household.name}'s orders or meals any more. You can invite them back.`,
      buttons: [
        { text: 'Cancel', role: 'cancel' },
        {
          text: self ? 'Leave' : 'Remove',
          role: 'destructive',
          handler: () => removeMember.mutate({ userId: member.user_id, self }),
        },
      ],
    })
  }

  function confirmCancel(invited: HouseholdInvite) {
    void presentAlert({
      header: `Withdraw ${invited.name}'s invitation?`,
      message: `${invited.email} won't be able to join unless you invite them again.`,
      buttons: [
        { text: 'Keep it', role: 'cancel' },
        { text: 'Withdraw', role: 'destructive', handler: () => cancelInvite.mutate({ inviteId: invited.id }) },
      ],
    })
  }

  return (
    <>
      {failed && <Notice>Couldn't do that: {failed.message}</Notice>}
      <Group
        title="Members"
        footerIcon="users"
        footer="Anyone here can invite or remove people. They'll also need TestFlight access to install the app."
      >
        {household.members.map((member) => (
          <Row
            key={member.user_id}
            // An account named nothing yet shows its email.
            label={member.name || member.email || 'Someone'}
            sub={member.name ? member.email : undefined}
            detail={member.user_id === userId ? 'You' : undefined}
            // The last member stays: a household is never empty.
            onClick={alone ? undefined : () => confirmRemove(member)}
            chevron={!alone}
          />
        ))}
        {household.invites.map((invited) => (
          <Row
            key={invited.id}
            label={invited.name}
            sub={invited.email}
            detail="Invited"
            chevron
            onClick={() => confirmCancel(invited)}
          />
        ))}
        <Row label="Invite someone" chevron onClick={inviteSomeone} />
      </Group>
    </>
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
      setSignOutError(errorMessage(error))
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
                  fields: [{ value: name ?? '', maxLength: NAME_LENGTH }],
                  onSave: ([text]) => changeName.mutate({ name: text }),
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
