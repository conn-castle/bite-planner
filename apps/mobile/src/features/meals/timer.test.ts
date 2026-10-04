import { createElement } from 'react'
import { renderToString } from 'react-dom/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../lib/log', () => ({ logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() }, errorFields: () => ({}) }))

// A notification as the app schedules it.
interface Scheduled {
  id: number
  title: string
  body: string
  schedule: { at: Date }
  sound?: string
  foreground?: boolean
  extra?: unknown
}

// The phone's notifications: what's scheduled to ring, and the permission iOS has.
const phone = vi.hoisted(() => ({
  permission: 'granted' as string,
  answer: 'granted' as string,
  scheduled: new Map<number, Scheduled>(),
  // iOS answers schedule() before it adds the notification (applying its requests
  // in order); the add can take a while, or fail.
  addDelayMs: 0,
  addFails: false,
  requests: Promise.resolve(),
  // How long reading what's pending takes.
  readDelayMs: 0,
}))
// No wait at all for no delay: the tests' clock moves only when they move it.
const later = (ms: number) => (ms ? new Promise((resolve) => setTimeout(resolve, ms)) : undefined)
const plugin = vi.hoisted(() => ({
  checkPermissions: vi.fn(async () => ({ display: phone.permission })),
  requestPermissions: vi.fn(async () => {
    phone.permission = phone.answer
    return { display: phone.permission }
  }),
  schedule: vi.fn(async ({ notifications }: { notifications: Scheduled[] }) => {
    const add = () => {
      if (!phone.addFails) for (const notification of notifications) phone.scheduled.set(notification.id, notification)
    }
    const delay = phone.addDelayMs
    if (delay) phone.requests = phone.requests.then(async () => (await later(delay), add()))
    else add()
    return { notifications }
  }),
  // As iOS reports them: the time to the second, as text.
  getPending: vi.fn(async () => {
    const pending = [...phone.scheduled.values()].map((notification) => ({
      ...notification,
      schedule: { at: notification.schedule.at.toISOString().replace(/\.\d+Z$/, 'Z') },
    }))
    await later(phone.readDelayMs)
    return { notifications: pending }
  }),
  cancel: vi.fn(async ({ notifications }: { notifications: { id: number }[] }) => {
    phone.requests = phone.requests.then(() => {
      for (const notification of notifications) phone.scheduled.delete(notification.id)
    })
    await phone.requests
  }),
}))
vi.mock('@capacitor/local-notifications', () => ({ LocalNotifications: plugin }))

const MEAL = { id: 'tacos', name: 'Sheet Pan Chicken Fajitas' }
const NOW = new Date('2026-10-04T17:00:00Z').getTime()

let timer: typeof import('./timer')

beforeEach(async () => {
  vi.useFakeTimers({ now: NOW })
  vi.resetModules()
  phone.permission = 'granted'
  phone.answer = 'granted'
  phone.scheduled.clear()
  phone.addFails = false
  phone.addDelayMs = 0
  phone.readDelayMs = 0
  phone.requests = Promise.resolve()
  for (const method of Object.values(plugin)) method.mockClear()
  timer = await import('./timer')
})

afterEach(() => {
  vi.useRealTimers()
})

const ringing = (): Scheduled[] => [...phone.scheduled.values()]

describe("a step's timer", () => {
  it('rings with a sound at the end of the step, even with the app closed', async () => {
    await timer.startTimer(MEAL, 3, 20)

    expect(ringing()).toEqual([
      expect.objectContaining({
        title: "Time's up",
        body: 'Sheet Pan Chicken Fajitas: step 4',
        schedule: expect.objectContaining({ at: new Date(NOW + 20 * 60_000) }),
        sound: 'timer.wav',
        foreground: true,
      }),
    ])
    expect(read(timer.useStepTimer)).toEqual({ mealId: 'tacos', step: 3, endsAt: NOW + 20 * 60_000 })
  })

  it('rings with a sound bundled with the app (iOS rings silently without one)', async () => {
    await timer.startTimer(MEAL, 0, 5)

    const [{ sound }] = ringing()
    // The files in public/, which the app is built with.
    expect(Object.keys(import.meta.glob('../../../public/*'))).toContain(`../../../public/${sound}`)
  })

  it('asks for notifications the first time, and runs once they are allowed', async () => {
    phone.permission = 'prompt'

    await timer.startTimer(MEAL, 0, 5)

    expect(plugin.requestPermissions).toHaveBeenCalledOnce()
    expect(ringing()).toHaveLength(1)
  })

  it("doesn't start when notifications are off, and says how to turn them on", async () => {
    phone.permission = 'prompt'
    phone.answer = 'denied'

    await expect(timer.startTimer(MEAL, 0, 5)).rejects.toThrow("Turn them on in the iPhone's Settings")
    phone.permission = 'denied'
    await expect(timer.startTimer(MEAL, 0, 5)).rejects.toBeInstanceOf(timer.TimersOff)

    expect(ringing()).toEqual([])
    expect(read(timer.useStepTimer)).toBeNull()
  })

  it('is the only one: starting another replaces it', async () => {
    await timer.startTimer(MEAL, 1, 10)
    await timer.startTimer({ id: 'chili', name: 'Chili' }, 2, 30)

    expect(ringing()).toEqual([expect.objectContaining({ body: 'Chili: step 3' })])
    expect(read(timer.useStepTimer)).toMatchObject({ mealId: 'chili', step: 2 })
  })

  it("won't ring once stopped", async () => {
    await timer.startTimer(MEAL, 1, 10)

    await timer.stopTimer()

    expect(ringing()).toEqual([])
    expect(read(timer.useStepTimer)).toBeNull()
  })

  it("is still running after the app closes and opens again, until it rings", async () => {
    await timer.startTimer(MEAL, 3, 20)
    vi.resetModules()
    timer = await import('./timer')
    expect(read(timer.useStepTimer)).toBeNull()

    expect(await timer.loadTimer()).toEqual({ mealId: 'tacos', step: 3, endsAt: NOW + 20 * 60_000 })
    expect(read(timer.useStepTimer)).toMatchObject({ mealId: 'tacos', step: 3 })
    vi.advanceTimersByTime(20 * 60_000)
    expect(read(timer.useStepTimer)).toBeNull()
  })

  it("doesn't run when the phone didn't set it", async () => {
    phone.addFails = true

    const started = timer.startTimer(MEAL, 0, 5)
    const failed = expect(started).rejects.toThrow("The phone didn't set the timer")
    await vi.advanceTimersByTimeAsync(3000)
    await failed

    expect(read(timer.useStepTimer)).toBeNull()
  })

  it('runs once iOS has it, however long that takes', async () => {
    phone.addDelayMs = 700

    const started = timer.startTimer(MEAL, 2, 15)
    await vi.advanceTimersByTimeAsync(1000)
    await started

    expect(read(timer.useStepTimer)).toMatchObject({ mealId: 'tacos', step: 2 })
  })

  it("won't ring when iOS sets it too late, once the cook was told it isn't set", async () => {
    phone.addDelayMs = 5000

    const started = timer.startTimer(MEAL, 2, 15)
    const failed = expect(started).rejects.toThrow("The phone didn't set the timer")
    await vi.advanceTimersByTimeAsync(6000)
    await failed

    expect(ringing()).toEqual([])
    expect(read(timer.useStepTimer)).toBeNull()
  })

  it('is not one of Castle Chef\'s other notifications waiting to ring', async () => {
    const at = new Date(NOW + 60_000)
    phone.scheduled.set(2, { id: 2, title: 'Other', body: '', schedule: { at }, extra: { mealId: 'tacos', step: 0 } })
    phone.scheduled.set(1, { id: 1, title: 'Old', body: '', schedule: { at } })

    expect(await timer.loadTimer()).toBeNull()
  })

  it('is what the cook last did: a read from before a start or stop changes nothing', async () => {
    phone.readDelayMs = 500
    const read1 = timer.loadTimer()
    phone.readDelayMs = 0
    await timer.startTimer(MEAL, 1, 10)
    await vi.advanceTimersByTimeAsync(500)

    expect(await read1).toBeNull()
    expect(read(timer.useStepTimer)).toMatchObject({ mealId: 'tacos', step: 1 })

    phone.addDelayMs = 300
    const started = timer.startTimer(MEAL, 2, 10)
    await timer.stopTimer()
    await vi.advanceTimersByTimeAsync(1000)
    await started

    expect(ringing()).toEqual([])
    expect(read(timer.useStepTimer)).toBeNull()
  })

  it('is over once it has rung', async () => {
    await timer.startTimer(MEAL, 1, 10)

    vi.advanceTimersByTime(10 * 60_000 - 1000)
    expect(read(timer.useStepTimer)).not.toBeNull()
    vi.advanceTimersByTime(1000)
    expect(read(timer.useStepTimer)).toBeNull()
  })

  it('keeps a timer started since an earlier one would have rung', async () => {
    await timer.startTimer(MEAL, 1, 10)
    await timer.startTimer(MEAL, 2, 30)

    vi.advanceTimersByTime(10 * 60_000)

    expect(read(timer.useStepTimer)).toMatchObject({ step: 2 })
  })
})

// The store behind the hook, read the way a component would (rendered once, on the server).
function read<T>(hook: () => T): T {
  let value: T | undefined
  function Probe() {
    value = hook()
    return null
  }
  renderToString(createElement(Probe))
  return value as T
}
