import { beforeEach, describe, expect, it, vi } from 'vitest'

// The outbox is saved with Capacitor Preferences; keep it in memory here.
const stored = new Map<string, string>()
vi.mock('@capacitor/preferences', () => ({
  Preferences: {
    get: async ({ key }: { key: string }) => ({ value: stored.get(key) ?? null }),
    set: async ({ key, value }: { key: string; value: string }) => void stored.set(key, value),
  },
}))

async function freshLog() {
  vi.resetModules()
  return import('./log')
}

beforeEach(() => {
  stored.clear()
  vi.spyOn(console, 'info').mockImplementation(() => {})
  vi.spyOn(console, 'debug').mockImplementation(() => {})
  vi.spyOn(console, 'warn').mockImplementation(() => {})
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

describe('log outbox', () => {
  it('keeps provider error codes and statuses for sign-in diagnostics', async () => {
    const { errorFields } = await freshLog()
    const error = Object.assign(new Error('Invalid OTP'), { code: 'INVALID_OTP', status: 400 })
    expect(errorFields(error)).toMatchObject({
      error: 'Invalid OTP',
      code: 'INVALID_OTP',
      status: 400,
    })
  })

  it('queues entries at info and above for upload, not debug ones', async () => {
    const { logger, nextLogBatch } = await freshLog()
    logger.debug('jobs.loaded')
    logger.info('app.start', { version: '1' })
    logger.error('render.crashed')

    const batch = await nextLogBatch(10)
    expect(batch.entries.map((entry) => [entry.level, entry.event])).toEqual([
      ['info', 'app.start'],
      ['error', 'render.crashed'],
    ])
    expect(batch.entries[0].version).toBe('1')
  })

  it('removes only the uploaded entries, keeping ones logged meanwhile', async () => {
    const { logger, nextLogBatch } = await freshLog()
    logger.info('first')
    const batch = await nextLogBatch(10)
    logger.info('during upload')
    batch.commit()

    expect((await nextLogBatch(10)).entries.map((entry) => entry.event)).toEqual(['during upload'])
  })

  it('keeps entries when an upload is never committed', async () => {
    const { logger, nextLogBatch } = await freshLog()
    logger.info('first')
    await nextLogBatch(10)

    expect((await nextLogBatch(10)).entries.map((entry) => entry.event)).toEqual(['first'])
  })

  it('reports entries dropped when the outbox is full', async () => {
    const { logger, nextLogBatch } = await freshLog()
    for (let index = 0; index < 1003; index++) logger.info('entry', { index })

    const batch = await nextLogBatch(2)
    expect(batch.entries.map((entry) => [entry.event, entry.count ?? entry.index])).toEqual([
      ['logs.dropped', 3],
      ['entry', 3],
      ['entry', 4],
    ])
    batch.commit()
    expect((await nextLogBatch(1)).entries.map((entry) => entry.event)).toEqual(['entry'])
  })

  it('saves errors at once, so they survive the app being killed right after', async () => {
    const first = await freshLog()
    first.logger.error('render.crashed')
    await vi.waitFor(() => expect(stored.size).toBe(1))

    const { nextLogBatch } = await freshLog()
    expect((await nextLogBatch(10)).entries.map((entry) => entry.event)).toEqual(['render.crashed'])
  })

  it('saves an upload at once, so a relaunch does not send it again', async () => {
    const first = await freshLog()
    first.logger.info('uploaded')
    const batch = await first.nextLogBatch(10)
    batch.commit()
    await vi.waitFor(() => expect(JSON.parse(stored.get('bite-planner.log-outbox') ?? '{}').entries).toEqual([]))

    const { nextLogBatch } = await freshLog()
    expect((await nextLogBatch(10)).entries).toEqual([])
  })

  it('reports a saved outbox it cannot read, and still queues what is logged after', async () => {
    stored.set('bite-planner.log-outbox', JSON.stringify({ entries: [{ event: 'no level or time' }], dropped: 0 }))
    const { logger, nextLogBatch } = await freshLog()
    logger.info('after launch')

    expect((await nextLogBatch(10)).entries.map((entry) => [entry.level, entry.event])).toEqual([
      ['error', 'logs.saved_outbox_unreadable'],
      ['info', 'after launch'],
    ])
  })

  it('restores entries saved before a relaunch', async () => {
    vi.useFakeTimers()
    const first = await freshLog()
    first.logger.info('before relaunch')
    await vi.runAllTimersAsync() // the debounced save
    vi.useRealTimers()

    const { nextLogBatch } = await freshLog()
    expect((await nextLogBatch(10)).entries.map((entry) => entry.event)).toEqual(['before relaunch'])
  })
})
