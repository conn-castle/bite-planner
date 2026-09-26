import { AppLauncher } from '@capacitor/app-launcher'
import { Capacitor } from '@capacitor/core'
import { type ActionPerformed, PushNotifications, type PushNotificationSchema } from '@capacitor/push-notifications'
import { MutationObserver, type QueryClient } from '@tanstack/react-query'
import { useSyncExternalStore } from 'react'
import { apiRequest } from '../../lib/api/client'
import { ApiError, isRetryable } from '../../lib/api/errors'
import type { GroceryOrder, NextOrder } from '../../lib/api/types'
import { isRecord } from '../../lib/isRecord'
import { errorFields, logger } from '../../lib/log'
import { formatClockTime } from '../../lib/dates'
import { queryKeys } from '../../lib/query/keys'
import { sessionQuery, useSession } from '../session/session'
import { usePersonalSettings } from '../settings/settings'

/**
 * Push notifications on the phone: the daily reminder to pick meals and "your
 * meal ideas are ready" (sent by the backend through Apple; see backend
 * reminders.py and push.py). This phone's token is registered for whoever is
 * signed in; iOS owns the permission, which the app only reads and asks for.
 */

/** Whether this is the iPhone app (a browser gets no push notifications). */
export const pushAvailable = Capacitor.getPlatform() === 'ios'

// ---------------------------------------------------------------------------
// A tiny store per piece of state the screens watch.

function store<T>(initial: T) {
  let value = initial
  const listeners = new Set<() => void>()
  return {
    get: () => value,
    set(next: T) {
      value = next
      for (const listener of listeners) listener()
    },
    subscribe(listener: () => void) {
      listeners.add(listener)
      return () => void listeners.delete(listener)
    },
  }
}

// ---------------------------------------------------------------------------
// Permission.

/** This phone's permission for Castle Chef's notifications, as iOS has it; null until read. */
export type Permission = 'on' | 'off' | 'not-set'

const permission = store<Permission | null>(null)

export function usePermission(): Permission | null {
  return useSyncExternalStore(permission.subscribe, permission.get, permission.get)
}

/**
 * The member's own daily reminder, for the screens that mention it: its time
 * ("8:00 AM"; null until the session has loaded), and whether it's off: turned
 * off in Settings, or this phone has Castle Chef's notifications off in iOS.
 */
export function useYourReminder(): { time: string | null; off: boolean } {
  const settings = usePersonalSettings()
  const current = usePermission()
  return {
    time: settings ? formatClockTime(settings.notification_time) : null,
    off: settings?.reminders_on === false || (pushAvailable && current === 'off'),
  }
}

/**
 * Whether "your meal ideas are ready" will reach this phone for a batch: the
 * signed-in member started it (it goes only to them) and has notifications on.
 */
export function useNotifiedOfIdeas(startedBy: string | null): boolean {
  const userId = useSession().data?.user.id
  const current = usePermission()
  return current === 'on' && startedBy !== null && startedBy === userId
}

/** Reads the permission from iOS again (it can change in iOS Settings while the app is away). */
export async function refreshPermission(): Promise<Permission> {
  const { receive } = await PushNotifications.checkPermissions()
  const current: Permission = receive === 'granted' ? 'on' : receive === 'denied' ? 'off' : 'not-set'
  permission.set(current)
  return current
}

/** iOS's "Allow notifications?" prompt (only the first time: iOS asks once), then registers if allowed. */
export async function askPermission(): Promise<void> {
  const mine = signIn
  const { receive } = await PushNotifications.requestPermissions()
  logger.info('push.permission_answered', { receive })
  if ((await refreshPermission()) === 'on') await registerPhone(mine)
}

// Counts sign-outs: setup and registrations begun before the latest one are
// stale (the phone was unregistered meanwhile) and must not register it again.
let signIn = 0
// The sign-in that last asked Apple to register this phone.
let registeredFor: number | null = null

/** Asks Apple for this phone's token (see the `registration` listener), unless its sign-in has ended. */
async function registerPhone(mine: number): Promise<void> {
  if (mine !== signIn) return
  registeredFor = mine
  await PushNotifications.register()
}

/** Castle Chef's page in iOS Settings, where notifications are turned on or off once asked. */
export async function openNotificationSettings(): Promise<void> {
  // UIApplication.openSettingsURLString.
  await AppLauncher.openUrl({ url: 'app-settings:' })
}

/**
 * Keeps this phone registered for the signed-in member: at launch and on every
 * return to the foreground (Apple's advice: the token can change). Asks for
 * permission once, the first time, right after sign-in. Failures are logged:
 * nothing on screen depends on them.
 */
export async function setUpPush(): Promise<void> {
  if (!pushAvailable) return
  const mine = signIn
  try {
    const current = await refreshPermission()
    if (mine !== signIn) return
    if (current === 'not-set') await askPermission()
    else if (current === 'on') await registerPhone(mine)
  } catch (error) {
    logger.warn('push.setup_failed', errorFields(error))
  }
}

// ---------------------------------------------------------------------------
// This phone's token, on the server.

const REGISTER_DEVICE = ['device', 'register']
const FORGET_DEVICE = ['device', 'forget']
// One at a time, in order: a sign-out's forget can't overtake a registration.
const DEVICE_CHANGES = { id: 'push-device' }
// How long signing out waits to forget this phone before going ahead (offline).
const FORGET_TIMEOUT_MS = 5000

// The token iOS gave this launch (null until then); what signing out forgets.
let deviceToken: string | null = null

/** How device registrations are sent; registered at startup, like other requests (see orders.ts). */
export function registerDeviceMutations(queryClient: QueryClient) {
  const retry = (failureCount: number, error: Error) => isRetryable(error, failureCount)
  queryClient.setMutationDefaults(REGISTER_DEVICE, {
    mutationFn: (token: string) => apiRequest<void>('PUT', `/v1/me/devices/${token}`),
    retry,
    scope: DEVICE_CHANGES,
  })
  queryClient.setMutationDefaults(FORGET_DEVICE, {
    mutationFn: (token: string) => apiRequest<void>('DELETE', `/v1/me/devices/${token}`),
    scope: DEVICE_CHANGES,
    // Signing out waits for it: fail at once offline rather than wait to be back online.
    networkMode: 'always',
  })
}

function signedInMember(queryClient: QueryClient): boolean {
  return Boolean(queryClient.getQueryData(sessionQuery.queryKey)?.household)
}

/**
 * Stops this phone getting the signed-out account's notifications: removed on
 * the server (if it can be, within a few seconds), and unregistered with Apple
 * on the phone either way, so an offline sign-out still stops them. The next
 * sign-in registers it again, for whoever that is.
 */
export async function forgetThisPhone(queryClient: QueryClient): Promise<void> {
  if (!pushAvailable) return
  signIn += 1
  registeredFor = null
  const token = deviceToken
  deviceToken = null
  if (token) {
    const forget = new MutationObserver<void, Error, string>(queryClient, { mutationKey: FORGET_DEVICE }).mutate(token)
    const timeout = new Promise<never>((_, reject) =>
      setTimeout(() => reject(new Error('Timed out forgetting this phone')), FORGET_TIMEOUT_MS),
    )
    try {
      await Promise.race([forget, timeout])
    } catch (error) {
      // The next account to sign in on this phone takes the token over.
      logger.warn('push.forget_failed', errorFields(error))
    }
  }
  try {
    await PushNotifications.unregister()
    await PushNotifications.removeAllDeliveredNotifications()
  } catch (error) {
    logger.warn('push.unregister_failed', errorFields(error))
  }
}

// ---------------------------------------------------------------------------
// Notifications arriving and being tapped.

/** What a notification carries for the app (backend push.py `Payload`). */
export type Delivered =
  | { kind: 'reminder'; orderNumber: number }
  | { kind: 'ideas_ready'; orderId: string }
  | { kind: 'test' }

export function parseDelivered(data: unknown): Delivered | null {
  if (!isRecord(data)) return null
  if (data.kind === 'reminder' && typeof data.order_number === 'number') {
    return { kind: 'reminder', orderNumber: data.order_number }
  }
  if (data.kind === 'ideas_ready' && typeof data.order_id === 'string') return { kind: 'ideas_ready', orderId: data.order_id }
  if (data.kind === 'test') return { kind: 'test' }
  return null
}

/**
 * What tapping a notification leads to: the order flow, when the household's
 * draft is still the one it's about; otherwise Home, with a note saying why, so
 * the tap doesn't seem to do nothing. `tappedOrder` is the order an "ideas
 * ready" was about, when it's no longer the draft ('discarded' once it's gone).
 */
export type TapResult = { open: true; orderId: string } | { open: false; header: string; message: string }

export function tapResult(
  tapped: Exclude<Delivered, { kind: 'test' }>,
  next: NextOrder,
  tappedOrder: GroceryOrder | 'discarded' | null,
): TapResult {
  if (tapped.kind === 'reminder') {
    const number = tapped.orderNumber
    if (next.number === number) {
      return next.draft
        ? { open: true, orderId: next.draft.id }
        : {
            open: false,
            header: `Order ${number} isn't started yet`,
            message: 'Nobody has picked meals for it. Tap Pick meals on its card to start it.',
          }
    }
    return next.number > number
      ? {
          open: false,
          header: `Order ${number} is already placed`,
          message: "It was placed after this reminder went out. Home shows what's next.",
        }
      : { open: false, header: `Order ${number} isn't open`, message: "Home shows what's next." }
  }
  if (next.draft?.id === tapped.orderId) return { open: true, orderId: tapped.orderId }
  if (tappedOrder === 'discarded') {
    return {
      open: false,
      header: 'That draft was discarded',
      message: "Its meal ideas went with it. Start a new order from Home when you're ready.",
    }
  }
  if (tappedOrder?.status === 'placed') {
    return {
      open: false,
      header: `Order ${tappedOrder.number} is already placed`,
      message: "It was placed before you opened its meal ideas. Home shows what's next.",
    }
  }
  return { open: false, header: "That order isn't open", message: "Home shows what's next." }
}

/**
 * Works out what a tap leads to from the server's current state: fetched here,
 * not through the next-order query, which another request (e.g. the flow's own
 * saving) may cancel.
 */
export async function resolveTap(about: Exclude<Delivered, { kind: 'test' }>): Promise<TapResult> {
  const next = await apiRequest<NextOrder>('GET', '/v1/grocery-orders/next')
  const tappedOrder =
    about.kind === 'ideas_ready' && next.draft?.id !== about.orderId
      ? await apiRequest<GroceryOrder>('GET', `/v1/grocery-orders/${about.orderId}`).catch((error: unknown) => {
          if (error instanceof ApiError && error.status === 404) return 'discarded' as const
          throw error
        })
      : null
  return tapResult(about, next, tappedOrder)
}

/** A notification the member tapped: what it's about, and when (to log how long opening it takes). */
export interface Tap {
  about: Delivered
  at: number
}

/** A tap waiting for Home to act on it (see WeekPage). */
const tapped = store<Tap | null>(null)

export function useTapped(): Tap | null {
  return useSyncExternalStore(tapped.subscribe, tapped.get, tapped.get)
}

/**
 * The draft an "ideas ready" tap opened before fresh data arrived (the phone's
 * connection can take seconds after waking): shown as loading its ideas, not as
 * the copy from before they were ready, until it's fetched again after `since`.
 */
export interface AwaitedIdeas {
  orderId: string
  since: number
}

const awaitedIdeas = store<AwaitedIdeas | null>(null)

export function useAwaitedIdeas(): AwaitedIdeas | null {
  return useSyncExternalStore(awaitedIdeas.subscribe, awaitedIdeas.get, awaitedIdeas.get)
}

export function awaitIdeas(orderId: string, since: number) {
  awaitedIdeas.set({ orderId, since })
}

/** The order is showing as fetched after the tap (or was closed): stop waiting, and log how long it took. */
export function stopAwaitingIdeas(outcome: 'shown' | 'closed') {
  const awaited = awaitedIdeas.get()
  if (!awaited) return
  awaitedIdeas.set(null)
  logger.info('push.ideas_awaited', { outcome, ms: Date.now() - awaited.since })
}

/**
 * Whether the flow shows an order as loading its ideas: an "ideas ready" tap
 * opened it and it hasn't been fetched since (a placeholder, or data older than
 * the tap), with no ideas in it yet.
 */
export function loadingIdeas(
  awaited: AwaitedIdeas | null,
  order: { id: string; hasIdeas: boolean; placeholder: boolean; fetchedAt: number },
): boolean {
  if (!awaited || awaited.orderId !== order.id || order.hasIdeas) return false
  return order.placeholder || order.fetchedAt < awaited.since
}

/** Home has acted on the tap (or decided there's nothing to open): it's done. */
export function clearTapped() {
  tapped.set(null)
}

/** In the app: a notification that arrived while it was open (iOS shows no banner then). */
export interface Toast {
  id: number
  title: string
  body: string
  /** What tapping it does (like tapping the notification). */
  delivered: Delivered
}

const toast = store<Toast | null>(null)
let toastCount = 0

export function useToast(): Toast | null {
  return useSyncExternalStore(toast.subscribe, toast.get, toast.get)
}

export function dismissToast() {
  toast.set(null)
}

/** Tapping the in-app toast: the same as tapping the notification. */
export function tapToast(shown: Toast) {
  toast.set(null)
  tapped.set({ about: shown.delivered, at: Date.now() })
}

// Whether the order flow is open: "ideas ready" needs no toast then (the ideas are showing).
let orderFlowOpen = false

export function setOrderFlowOpen(open: boolean) {
  orderFlowOpen = open
}

function received(notification: PushNotificationSchema, queryClient: QueryClient) {
  const delivered = parseDelivered(notification.data)
  logger.info('push.received', { kind: delivered?.kind ?? null })
  // Whatever it's about, Home may be showing something older.
  void queryClient.invalidateQueries({ queryKey: queryKeys.nextOrder })
  if (delivered?.kind === 'ideas_ready' && !orderFlowOpen) {
    toast.set({
      id: ++toastCount,
      title: notification.title ?? 'Your meal ideas are ready',
      body: notification.body ?? '',
      delivered,
    })
  }
}

function tappedNotification(action: ActionPerformed) {
  const delivered = parseDelivered(action.notification.data)
  logger.info('push.tapped', { kind: delivered?.kind ?? null })
  // A test notification only opens the app.
  if (delivered && delivered.kind !== 'test') tapped.set({ about: delivered, at: Date.now() })
}

/**
 * Listens for this phone's token and for notifications, once, at startup (a tap
 * that launched the app is delivered as soon as the listener is added).
 */
export function listenForPush(queryClient: QueryClient) {
  if (!pushAvailable) return
  void PushNotifications.addListener('registration', ({ value }) => {
    if (registeredFor !== signIn) {
      // Asked for before a sign-out that has since unregistered the phone: undo it.
      logger.info('push.stale_registration')
      void PushNotifications.unregister().catch((error: unknown) =>
        logger.warn('push.unregister_failed', errorFields(error)),
      )
      return
    }
    deviceToken = value
    if (!signedInMember(queryClient)) return
    void new MutationObserver<void, Error, string>(queryClient, { mutationKey: REGISTER_DEVICE })
      .mutate(value)
      .catch(() => undefined) // logged by the mutation cache
  })
  void PushNotifications.addListener('registrationError', (error) =>
    logger.warn('push.registration_failed', { error: error.error }),
  )
  void PushNotifications.addListener('pushNotificationReceived', (notification) => received(notification, queryClient))
  void PushNotifications.addListener('pushNotificationActionPerformed', tappedNotification)
}
