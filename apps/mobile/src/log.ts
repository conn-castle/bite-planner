import { Preferences } from '@capacitor/preferences'

/**
 * Structured app logging. Each entry is `{ ts, level, event, launch, ...fields }`.
 * Entries go to the console and to a ring buffer persisted on the device, which
 * the Account page can send to the household's machine (see sendLogs in neon.ts).
 * Never log secrets (sign-in codes, tokens).
 */

export type LogLevel = 'debug' | 'info' | 'warn' | 'error'
export type LogFields = Record<string, unknown>
export interface LogEntry extends LogFields {
  ts: string
  level: LogLevel
  event: string
  launch: string
}

const STORAGE_KEY = 'castle-chef.logs'
const MAX_ENTRIES = 1000
const SAVE_DELAY_MS = 1000
// Groups the entries of one app launch.
const LAUNCH = crypto.randomUUID()

let entries: LogEntry[] = []
let saveTimer: ReturnType<typeof setTimeout> | undefined

// Entries logged before the stored buffer loads are kept after the stored ones.
const loaded = Preferences.get({ key: STORAGE_KEY }).then(({ value }) => {
  if (!value) return
  try {
    entries = [...(JSON.parse(value) as LogEntry[]), ...entries].slice(-MAX_ENTRIES)
  } catch (error) {
    write('error', 'logs.stored_buffer_unreadable', errorFields(error))
  }
})

function save() {
  saveTimer = undefined
  Preferences.set({ key: STORAGE_KEY, value: JSON.stringify(entries) }).catch((error: unknown) =>
    console.error('logs.save_failed', error),
  )
}

function write(level: LogLevel, event: string, fields: LogFields = {}) {
  const entry: LogEntry = { ts: new Date().toISOString(), level, event, launch: LAUNCH, ...fields }
  entries.push(entry)
  if (entries.length > MAX_ENTRIES) entries = entries.slice(-MAX_ENTRIES)
  console[level](event, fields)
  saveTimer ??= setTimeout(save, SAVE_DELAY_MS)
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
  if (typeof error === 'object' && error !== null && 'message' in error) {
    const { message, code, status } = error as { message: unknown; code?: unknown; status?: unknown }
    return { error: String(message), code, status }
  }
  return { error: String(error) }
}

export async function getLogEntries(): Promise<LogEntry[]> {
  await loaded
  return [...entries]
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
