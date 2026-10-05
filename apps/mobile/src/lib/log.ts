import { Preferences } from '@capacitor/preferences'
import { errorMessage } from './errorMessage'
import { isRecord } from './isRecord'

/**
 * Structured app logging. Each entry is `{ ts, level, event, launch, ...fields }`.
 * Every entry goes to the console; entries at info and above also wait in an
 * outbox saved on the device until they are uploaded to the API (see
 * logUpload.ts), so they survive going offline and relaunches.
 * Log `error` only for defects (crashes, uncaught exceptions): the API turns
 * app errors into alarm emails. Expected failures (offline, a refused request)
 * are `warn`. Never log secrets (sign-in codes, tokens).
 */

const LEVELS = ['debug', 'info', 'warn', 'error'] as const
export type LogLevel = (typeof LEVELS)[number]
export type LogFields = Record<string, unknown>
export interface LogEntry extends LogFields {
  ts: string
  level: LogLevel
  event: string
  launch: string
}

const STORAGE_KEY = 'castle-chef.log-outbox'
// Past this, the oldest unsent entries are dropped (and the drop is reported).
const MAX_ENTRIES = 1000
const SAVE_DELAY_MS = 1000
// Groups the entries of one app launch.
const LAUNCH = crypto.randomUUID()

interface Outbox {
  entries: LogEntry[]
  // Entries dropped because the outbox was full, not yet reported.
  dropped: number
}

const isLogEntry = (value: unknown): value is LogEntry =>
  isRecord(value) &&
  typeof value.ts === 'string' &&
  LEVELS.some((level) => level === value.level) &&
  typeof value.event === 'string' &&
  typeof value.launch === 'string'

const isOutbox = (value: unknown): value is Outbox =>
  isRecord(value) && Array.isArray(value.entries) && value.entries.every(isLogEntry) && typeof value.dropped === 'number'

let outbox: Outbox = { entries: [], dropped: 0 }
let saveTimer: ReturnType<typeof setTimeout> | undefined

// Entries logged before the saved outbox loads are kept after the saved ones.
const loaded = Preferences.get({ key: STORAGE_KEY }).then(({ value }) => {
  if (!value) return
  try {
    const saved: unknown = JSON.parse(value)
    if (!isOutbox(saved)) throw new Error('The saved outbox has an unknown shape.')
    outbox = { entries: [...saved.entries, ...outbox.entries], dropped: saved.dropped + outbox.dropped }
    trim()
  } catch (error) {
    write('error', 'logs.saved_outbox_unreadable', errorFields(error))
  }
})

function trim() {
  const excess = outbox.entries.length - MAX_ENTRIES
  if (excess <= 0) return
  outbox.entries = outbox.entries.slice(excess)
  outbox.dropped += excess
}

function save() {
  saveTimer = undefined
  // After the saved outbox has loaded and merged, or it would be overwritten.
  void loaded
    .then(() => Preferences.set({ key: STORAGE_KEY, value: JSON.stringify(outbox) }))
    .catch((error: unknown) => console.error('logs.save_failed', error))
}

function scheduleSave() {
  saveTimer ??= setTimeout(save, SAVE_DELAY_MS)
}

/** Saves the outbox now rather than after the usual delay (e.g. before iOS suspends the app). */
export function flushLogs() {
  clearTimeout(saveTimer)
  save()
}

function write(level: LogLevel, event: string, fields: LogFields = {}) {
  console[level](event, fields)
  if (level === 'debug') return
  outbox.entries.push({ ts: new Date().toISOString(), level, event, launch: LAUNCH, ...fields })
  trim()
  // An error may be the last thing before the app is killed: keep it now.
  if (level === 'error') flushLogs()
  else scheduleSave()
}

export const logger = {
  debug: (event: string, fields?: LogFields) => write('debug', event, fields),
  info: (event: string, fields?: LogFields) => write('info', event, fields),
  warn: (event: string, fields?: LogFields) => write('warn', event, fields),
  error: (event: string, fields?: LogFields) => write('error', event, fields),
}

/** Fields describing an error, for `logger.error('x.failed', errorFields(e))`. */
export function errorFields(error: unknown): LogFields {
  if (error instanceof Error) {
    return { error: error.message, errorName: error.name, stack: error.stack }
  }
  // neon-js returns plain error objects: { message, code, status }.
  if (isRecord(error) && 'message' in error) {
    return { error: errorMessage(error), code: error.code, status: error.status }
  }
  return { error: String(error) }
}

export interface LogBatch {
  entries: LogEntry[]
  /** Removes the batch's entries from the outbox, once the API has them. */
  commit(): void
}

/**
 * The oldest unsent entries, at most `limit`, led by a report of any entries
 * dropped while the outbox was full.
 */
export async function nextLogBatch(limit: number): Promise<LogBatch> {
  await loaded
  const batch = outbox.entries.slice(0, limit)
  const dropped = outbox.dropped
  const report: LogEntry[] = dropped
    ? [{ ts: new Date().toISOString(), level: 'warn', event: 'logs.dropped', launch: LAUNCH, count: dropped }]
    : []
  return {
    entries: [...report, ...batch],
    commit() {
      const sent = new Set(batch)
      outbox.entries = outbox.entries.filter((entry) => !sent.has(entry))
      outbox.dropped -= dropped
      // Now, so an upload finished just before iOS suspends the app isn't sent again.
      flushLogs()
    },
  }
}

/** Logs uncaught errors and unhandled promise rejections. */
export function logUncaughtErrors() {
  window.addEventListener('error', (event) => {
    logger.error('window.error', { ...errorFields(event.error ?? event.message) })
  })
  window.addEventListener('unhandledrejection', (event) => {
    logger.error('promise.unhandled', errorFields(event.reason))
  })
}
