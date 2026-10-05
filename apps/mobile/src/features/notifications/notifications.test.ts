import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement } from 'react'
import { renderToString } from 'react-dom/server'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiRequest } from '../../lib/api/client'
import type { GroceryOrder, NextOrder } from '../../lib/api/types'
import { queryKeys } from '../../lib/query/keys'

vi.mock('../../lib/api/client', () => ({ apiRequest: vi.fn() }))
vi.mock('../../lib/log', () => ({ logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() }, errorFields: () => ({}) }))
// The session module these import signs in through Neon Auth, which a test doesn't have.
vi.mock('../../lib/auth/auth', () => ({ auth: {} }))
vi.mock('@capacitor/core', () => ({ Capacitor: { getPlatform: () => 'ios', isNativePlatform: () => true } }))
vi.mock('@capacitor/app-launcher', () => ({ AppLauncher: { openUrl: vi.fn() } }))

// The push plugin: what the app asked of it, and a way to fire its events.
type Listener = (event: never) => void
const listeners = new Map<string, Listener>()
const plugin = vi.hoisted(() => ({
  register: vi.fn(async () => {}),
  unregister: vi.fn(async () => {}),
  removeAllDeliveredNotifications: vi.fn(async () => {}),
  checkPermissions: vi.fn(async () => ({ receive: 'granted' })),
  requestPermissions: vi.fn(async () => ({ receive: 'granted' })),
  addListener: vi.fn(),
}))
vi.mock('@capacitor/push-notifications', () => ({ PushNotifications: plugin }))
plugin.addListener.mockImplementation(async (event: string, listener: Listener) => {
  listeners.set(event, listener)
  return { remove: async () => {} }
})
function fire(event: string, payload: unknown) {
  const listener = listeners.get(event)
  if (!listener) throw new Error(`Nothing listens for ${event}`)
  listener(payload as never)
}

const request = vi.mocked(apiRequest)
const PHONE = 'ab'.repeat(32)

let queryClient: QueryClient
// A fresh copy each test: the module keeps this launch's token and what's showing.
let push: typeof import('./notifications')

beforeEach(async () => {
  vi.resetModules()
  listeners.clear()
  request.mockReset()
  request.mockResolvedValue(undefined)
  for (const method of Object.values(plugin)) method.mockClear()
  queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  push = await import('./notifications')
  push.registerDeviceMutations(queryClient)
  push.listenForPush(queryClient)
  signedIn(true)
})

function signedIn(member: boolean) {
  queryClient.setQueryData(['session'], {
    user: { id: 'sam', email: 'sam@example.com', name: 'Sam' },
    household: member ? { id: 'castle', name: 'Conn Castle' } : null,
    settings: null,
  })
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0))

describe("this phone's token", () => {
  // The token arrives after the app asks Apple for it.
  beforeEach(() => push.setUpPush())

  it('is registered for the member signed in', async () => {
    fire('registration', { value: PHONE })
    await settle()

    expect(request.mock.calls).toEqual([['PUT', `/v1/me/devices/${PHONE}`]])
  })

  it('is not registered for someone who is not in a household', async () => {
    signedIn(false)
    fire('registration', { value: PHONE })
    await settle()

    expect(request).not.toHaveBeenCalled()
  })

  it('is forgotten on sign-out, after any registration still on its way', async () => {
    let finishRegistering = () => {}
    request.mockImplementationOnce(() => new Promise((resolve) => (finishRegistering = () => resolve(undefined))))
    fire('registration', { value: PHONE })
    await settle()

    const signingOut = push.forgetThisPhone(queryClient)
    await settle()
    expect(request.mock.calls).toEqual([['PUT', `/v1/me/devices/${PHONE}`]])
    finishRegistering()
    await signingOut

    expect(request.mock.calls).toEqual([
      ['PUT', `/v1/me/devices/${PHONE}`],
      ['DELETE', `/v1/me/devices/${PHONE}`],
    ])
    expect(plugin.unregister).toHaveBeenCalled()
    expect(plugin.removeAllDeliveredNotifications).toHaveBeenCalled()
  })

  it('stops the phone getting notifications even when the server cannot be told', async () => {
    fire('registration', { value: PHONE })
    await settle()
    request.mockRejectedValueOnce(new Error('offline'))

    await push.forgetThisPhone(queryClient)

    expect(plugin.unregister).toHaveBeenCalled()
  })
})

describe('signing out while push is being set up', () => {
  it("doesn't let a setup still waiting on iOS register the phone again", async () => {
    let answer: (value: { receive: string }) => void = () => {}
    plugin.checkPermissions.mockImplementationOnce(() => new Promise((resolve) => (answer = resolve)))
    const setup = push.setUpPush()

    await push.forgetThisPhone(queryClient)
    answer({ receive: 'granted' })
    await setup

    expect(plugin.register).not.toHaveBeenCalled()
  })

  it('undoes a registration that lands after the phone was unregistered', async () => {
    await push.setUpPush()
    expect(plugin.register).toHaveBeenCalledOnce()
    await push.forgetThisPhone(queryClient)
    plugin.unregister.mockClear()

    fire('registration', { value: PHONE })
    await settle()

    expect(plugin.unregister).toHaveBeenCalledOnce()
    expect(request).not.toHaveBeenCalled()
  })
})

describe('permission', () => {
  it('is asked for once, the first time, then the phone registers', async () => {
    plugin.checkPermissions.mockResolvedValueOnce({ receive: 'prompt' })

    await push.setUpPush()

    expect(plugin.requestPermissions).toHaveBeenCalledOnce()
    expect(plugin.register).toHaveBeenCalledOnce()
  })

  it('is not asked for again once answered, and a refusal registers nothing', async () => {
    plugin.checkPermissions.mockResolvedValue({ receive: 'denied' })

    await push.setUpPush()

    expect(plugin.requestPermissions).not.toHaveBeenCalled()
    expect(plugin.register).not.toHaveBeenCalled()
    plugin.checkPermissions.mockResolvedValue({ receive: 'granted' })
  })
})

describe('"ideas ready" reaching this phone', () => {
  const notified = (startedBy: string | null) =>
    read(() => push.useNotifiedOfJob(startedBy), (children) => createElement(QueryClientProvider, { client: queryClient }, children))

  it('is only for the member who started the batch, with notifications on', async () => {
    await push.refreshPermission()
    expect(notified('sam')).toBe(true)
    expect(notified('alex')).toBe(false)
    expect(notified(null)).toBe(false)

    plugin.checkPermissions.mockResolvedValueOnce({ receive: 'denied' })
    await push.refreshPermission()
    expect(notified('sam')).toBe(false)
  })
})

describe('a notification arriving while the app is open', () => {
  const ideas = { title: 'Your meal ideas are ready', body: '20 ideas for Order 4.', data: { kind: 'ideas_ready', order_id: 'draft' } }

  it('shows "ideas ready" in the app, and refreshes Home', () => {
    const refreshed = vi.spyOn(queryClient, 'invalidateQueries')
    fire('pushNotificationReceived', ideas)

    expect(currentToast()).toMatchObject({
      title: 'Your meal ideas are ready',
      body: '20 ideas for Order 4.',
      delivered: { kind: 'ideas_ready', orderId: 'draft' },
    })
    expect(refreshed).toHaveBeenCalledWith({ queryKey: queryKeys.nextOrder })
  })

  it('shows nothing for "ideas ready" while the order flow is open, or for a reminder', () => {
    const closeFlow = push.orderFlowOpened()
    fire('pushNotificationReceived', ideas)
    closeFlow()
    fire('pushNotificationReceived', {
      title: 'Pick meals for Order 4',
      body: 'Decide by Saturday.',
      data: { kind: 'reminder', order_number: 4 },
    })

    expect(currentToast()).toBeNull()
  })

  it('shows nothing for "ideas ready" while a flow is open, though another opened and closed meanwhile', () => {
    const closeHomeFlow = push.orderFlowOpened()
    // A booking notification tapped meanwhile opens an order page, whose flow opens and closes.
    push.orderFlowOpened()()
    fire('pushNotificationReceived', ideas)
    closeHomeFlow()

    expect(currentToast()).toBeNull()
  })

  it('opens what it is about when the toast is tapped', () => {
    fire('pushNotificationReceived', ideas)
    const toast = currentToast()
    if (!toast) throw new Error('No toast')

    push.tapToast(toast)

    expect(currentToast()).toBeNull()
    expect(currentTapped()).toEqual({ about: { kind: 'ideas_ready', orderId: 'draft' }, at: expect.any(Number) })
  })
})

describe('a tapped notification', () => {
  it('waits for Home to open what it is about', () => {
    fire('pushNotificationActionPerformed', { notification: { data: { kind: 'reminder', order_number: 4 } } })
    expect(currentTapped()).toEqual({ about: { kind: 'reminder', orderNumber: 4 }, at: expect.any(Number) })

    push.clearTapped()
    expect(currentTapped()).toBeNull()
  })

  it('is handed to Home whether it launched the app or came after Home was listening', () => {
    fire('pushNotificationActionPerformed', { notification: { data: { kind: 'reminder', order_number: 4 } } })
    const handled: unknown[] = []
    const stop = push.onTap((tap) => handled.push(tap.about))
    expect(handled).toEqual([{ kind: 'reminder', orderNumber: 4 }])

    push.clearTapped()
    fire('pushNotificationActionPerformed', { notification: { data: { kind: 'list_ready', order_id: 'draft' } } })
    stop()
    fire('pushNotificationActionPerformed', { notification: { data: { kind: 'reminder', order_number: 5 } } })

    expect(handled).toEqual([
      { kind: 'reminder', orderNumber: 4 },
      { kind: 'list_ready', orderId: 'draft' },
    ])
  })

  it('only opens the app when it is a test, or not one the app knows', () => {
    fire('pushNotificationActionPerformed', { notification: { data: { kind: 'test' } } })
    fire('pushNotificationActionPerformed', { notification: { data: { kind: 'reminder' } } })
    expect(currentTapped()).toBeNull()
  })
})

// The stores behind the hooks, read the way a component would (rendered once, on the server).
function read<T>(hook: () => T, wrap = (children: ReturnType<typeof createElement>) => children): T {
  let value: T | undefined
  function Probe() {
    value = hook()
    return null
  }
  renderToString(wrap(createElement(Probe)))
  return value as T
}

const currentToast = () => read(push.useToast)
const currentTapped = () => read(push.useTapped)

describe('an order opened from "ideas ready" before fresh data arrived', () => {
  const awaited = { orderId: 'd', since: 1000 }
  const shown = (fetchedAt: number, hasIdeas = false, placeholder = false) => ({ id: 'd', hasIdeas, placeholder, fetchedAt })

  it('shows as loading its ideas until it has been fetched since the tap', () => {
    expect(push.loadingIdeas(awaited, shown(500))).toBe(true)
    expect(push.loadingIdeas(awaited, shown(2000, false, true))).toBe(true)
    // Fetched since: shown as it is, ideas or not (a failed batch shows why).
    expect(push.loadingIdeas(awaited, shown(2000))).toBe(false)
  })

  it('has the order fetched again, however recently its copy was cached', () => {
    queryClient.setQueryData(queryKeys.order('d'), { id: 'd' })

    push.awaitIdeas(queryClient, 'd', Date.now())

    expect(queryClient.getQueryState(queryKeys.order('d'))?.isInvalidated).toBe(true)
    expect(read(push.useAwaitedIdeas)).toMatchObject({ orderId: 'd' })
  })

  it('shows at once when the copy on the phone already has the ideas, or it is another order', () => {
    expect(push.loadingIdeas(awaited, shown(500, true))).toBe(false)
    expect(push.loadingIdeas(awaited, { ...shown(500), id: 'other' })).toBe(false)
    expect(push.loadingIdeas(null, shown(500))).toBe(false)
  })
})

describe('what a tap leads to', () => {
  const draft = (id: string, number: number, status: 'draft' | 'placed' = 'draft'): GroceryOrder => ({
    book_state: null,
    book_on: null,
    booking: null,
    book_issue: null,
    id,
    number,
    status,
    created_by: { user_id: 'sam', name: 'Sam' },
    created_at: '2026-09-24T09:00:00Z',
    target_date: '2026-09-28',
    decide_by_date: '2026-09-27',
    reminders_start_date: '2026-09-25',
    earliest_delivery_date: '2026-09-25',
    delivery_date: null,
    delivery_by: null,
    next_delivery_date: null,
    reached_step: 'pick',
    jobs: [],
    picks: [],
    lock: null,
    shopping_list: null,
    quote: null,
    best_window: null,
    fallback_window: null,
  })
  const next = (number: number, current: GroceryOrder | null): NextOrder => ({
    number,
    target_date: '2026-09-28',
    decide_by_date: '2026-09-27',
    reminders_start_date: '2026-09-25',
    earliest_delivery_date: '2026-09-25',
    draft: current,
  })

  it('opens the order flow when the draft is the one it is about', () => {
    expect(push.tapResult({ kind: 'reminder', orderNumber: 4 }, next(4, draft('d', 4)), null)).toEqual({
      open: true,
      orderId: 'd',
    })
    expect(push.tapResult({ kind: 'ideas_ready', orderId: 'd' }, next(4, draft('d', 4)), null)).toEqual({
      open: true,
      orderId: 'd',
    })
  })

  it('opens a "list ready" at the shopping list, and says why not when its draft is gone or placed', () => {
    expect(push.tapResult({ kind: 'list_ready', orderId: 'd' }, next(4, draft('d', 4)), null)).toEqual({
      open: true,
      orderId: 'd',
      step: 'list',
    })
    expect(push.tapResult({ kind: 'list_ready', orderId: 'd' }, next(4, null), 'discarded')).toMatchObject({
      open: false,
      message: "Its shopping list went with it. Start a new order from Home when you're ready.",
    })
    expect(push.tapResult({ kind: 'list_ready', orderId: 'd' }, next(5, null), draft('d', 4, 'placed'))).toMatchObject({
      open: false,
      header: 'Order 4 is already placed',
    })
  })

  it('opens "couldn\'t shop your list" at the shopping list too, where it can be tried again', () => {
    expect(push.parseDelivered({ kind: 'list_failed', order_id: 'd' })).toEqual({ kind: 'list_failed', orderId: 'd' })
    expect(push.tapResult({ kind: 'list_failed', orderId: 'd' }, next(4, draft('d', 4)), null)).toEqual({
      open: true,
      orderId: 'd',
      step: 'list',
    })
  })

  it('reads how booking went from a notification: about that order', () => {
    expect(push.parseDelivered({ kind: 'booking', order_id: 'o' })).toEqual({ kind: 'booking', orderId: 'o' })
    expect(push.parseDelivered({ kind: 'booking' })).toBeNull()
  })

  it('reads "list ready" from a notification', () => {
    expect(push.parseDelivered({ kind: 'list_ready', order_id: 'd' })).toEqual({ kind: 'list_ready', orderId: 'd' })
    expect(push.parseDelivered({ kind: 'list_ready' })).toBeNull()
  })

  it('says why not when the reminder is for an order placed since, or not started', () => {
    expect(push.tapResult({ kind: 'reminder', orderNumber: 4 }, next(5, null), null)).toMatchObject({
      open: false,
      header: 'Order 4 is already placed',
    })
    expect(push.tapResult({ kind: 'reminder', orderNumber: 4 }, next(4, null), null)).toMatchObject({
      open: false,
      header: "Order 4 isn't started yet",
    })
  })

  it("is worked out from the server's current next order, asking after the tapped order only when needed", async () => {
    // The module's own copy (modules are fresh for each test).
    const { ApiError } = await import('../../lib/api/errors')
    request.mockImplementation(async (_method, path) => {
      if (path === '/v1/grocery-orders/next') return next(5, null)
      throw new ApiError(404, 'Not found.')
    })

    await expect(push.resolveTap({ kind: 'ideas_ready', orderId: 'd' })).resolves.toMatchObject({
      open: false,
      header: 'That draft was discarded',
    })
    expect(request.mock.calls).toEqual([
      ['GET', '/v1/grocery-orders/next'],
      ['GET', '/v1/grocery-orders/d'],
    ])

    request.mockReset()
    request.mockResolvedValue(next(4, draft('d', 4)))
    await expect(push.resolveTap({ kind: 'reminder', orderNumber: 4 })).resolves.toEqual({ open: true, orderId: 'd' })
    expect(request.mock.calls).toEqual([['GET', '/v1/grocery-orders/next']])
  })

  it('fails when the server cannot be reached, rather than guessing', async () => {
    request.mockRejectedValue(new TypeError('Load failed'))
    await expect(push.resolveTap({ kind: 'reminder', orderNumber: 4 })).rejects.toThrow('Load failed')
  })

  it('says why not when the ideas were for a draft discarded or placed since', () => {
    expect(push.tapResult({ kind: 'ideas_ready', orderId: 'd' }, next(4, null), 'discarded')).toMatchObject({
      open: false,
      header: 'That draft was discarded',
    })
    expect(
      push.tapResult({ kind: 'ideas_ready', orderId: 'd' }, next(5, draft('e', 5)), draft('d', 4, 'placed')),
    ).toMatchObject({ open: false, header: 'Order 4 is already placed' })
  })
})
