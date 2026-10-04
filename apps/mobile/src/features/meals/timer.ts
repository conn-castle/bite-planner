import { LocalNotifications } from '@capacitor/local-notifications'
import { useSyncExternalStore } from 'react'
import { isRecord } from '../../lib/isRecord'
import { logger } from '../../lib/log'
import { store } from '../../lib/store'

/**
 * A cooking step's timer. It is the phone notification that rings it,
 * scheduled when it starts (so it rings with the phone locked or the app
 * closed): the timer running is the one iOS has waiting to ring, read back
 * from iOS. One runs at a time, until it rings or the cook stops it.
 */
export interface StepTimer {
  mealId: string
  /** The step's position in the meal's steps. */
  step: number
  /** When it rings, in milliseconds since the epoch. */
  endsAt: number
}

// The notification that rings it: always this one, as one timer runs at a time.
const NOTIFICATION_ID = 1
// In public/ (bundled with the app): without a sound, iOS shows the notification silently.
const SOUND = 'timer.wav'
// iOS schedules to the second.
const SCHEDULE_PRECISION_MS = 1000
// How long iOS may take to have a new timer waiting (scheduling answers before it does).
const SCHEDULE_WAIT_MS = 2000

const running = store<StepTimer | null>(null)
let rings: ReturnType<typeof setTimeout> | undefined
// Each start, stop, and read counts up: only the latest one's answer is shown.
let latest = 0

export function useStepTimer(): StepTimer | null {
  return useSyncExternalStore(running.subscribe, running.get, running.get)
}

/** The phone's notifications are off for Castle Chef, so a timer couldn't ring. */
export class TimersOff extends Error {
  constructor() {
    super("Timers ring with a notification, and Castle Chef's are off. Turn them on in the iPhone's Settings.")
  }
}

function show(timer: StepTimer | null) {
  running.set(timer)
  clearTimeout(rings)
  // Over once it has rung (the notification did the ringing). A timeout held
  // up while the app was in the background runs as it comes back.
  if (timer)
    rings = setTimeout(() => {
      if (running.get() === timer) running.set(null)
    }, timer.endsAt - Date.now())
}

/** The timer iOS has waiting to ring, if any. */
async function waiting(): Promise<StepTimer | null> {
  const { notifications } = await LocalNotifications.getPending()
  const pending = notifications.find((notification) => notification.id === NOTIFICATION_ID)
  const extra: unknown = pending?.extra
  const at = pending?.schedule?.at
  if (!isRecord(extra) || typeof extra.mealId !== 'string' || typeof extra.step !== 'number' || !at) return null
  return { mealId: extra.mealId, step: extra.step, endsAt: new Date(at).getTime() }
}

/**
 * Reads the timer running from iOS (one may have been started before the app
 * last closed), and returns it; null too when a start or stop came since.
 */
export async function loadTimer(): Promise<StepTimer | null> {
  const turn = ++latest
  const timer = await waiting()
  if (turn !== latest) return null
  show(timer)
  return timer
}

/** Starts a step's timer, in place of any running. Asks for notifications first if they never were. */
export async function startTimer(meal: { id: string; name: string }, step: number, minutes: number): Promise<void> {
  const turn = ++latest
  let { display } = await LocalNotifications.checkPermissions()
  if (display === 'prompt' || display === 'prompt-with-rationale') ({ display } = await LocalNotifications.requestPermissions())
  if (display !== 'granted') throw new TimersOff()
  if (turn !== latest) return
  const endsAt = Date.now() + minutes * 60_000
  // Scheduling under the same id replaces a timer already running.
  await LocalNotifications.schedule({
    notifications: [
      {
        id: NOTIFICATION_ID,
        title: "Time's up",
        body: `${meal.name}: step ${step + 1}`,
        schedule: { at: new Date(endsAt), allowWhileIdle: true },
        sound: SOUND,
        // Rings in the app too (push notifications show no banner there).
        foreground: true,
        extra: { mealId: meal.id, step },
      },
    ],
  })
  // It runs once iOS has it waiting to ring. A start or stop since takes over.
  const deadline = Date.now() + SCHEDULE_WAIT_MS
  for (;;) {
    const timer = await waiting()
    if (turn !== latest) return
    if (timer?.mealId === meal.id && timer.step === step && Math.abs(timer.endsAt - endsAt) <= SCHEDULE_PRECISION_MS) {
      show(timer)
      logger.info('cooking.timer_started', { minutes })
      return
    }
    if (Date.now() > deadline) {
      // In case iOS sets it yet: it mustn't ring once the cook is told it isn't set.
      await stopTimer()
      throw new Error("The phone didn't set the timer. Try again.")
    }
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
}

/** Stops the running timer: it won't ring. */
export async function stopTimer(): Promise<void> {
  const turn = ++latest
  await LocalNotifications.cancel({ notifications: [{ id: NOTIFICATION_ID }] })
  if (turn === latest) show(null)
  logger.info('cooking.timer_stopped')
}
