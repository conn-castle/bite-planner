import { beforeEach, describe, expect, it, vi } from 'vitest'

const stored = new Map<string, string>()
vi.mock('@capacitor/preferences', () => ({
  Preferences: {
    get: async ({ key }: { key: string }) => ({ value: stored.get(key) ?? null }),
    set: async ({ key, value }: { key: string; value: string }) => void stored.set(key, value),
  },
}))
vi.mock('@capacitor/device', () => ({
  Device: { getInfo: async () => ({ model: 'iPhone', operatingSystem: 'ios', osVersion: '26', webViewVersion: '26' }) },
}))
const apiRequest = vi.fn()
vi.mock('./api/client', () => ({ apiRequest: (...args: unknown[]) => apiRequest(...args) }))

beforeEach(() => {
  stored.clear()
  apiRequest.mockReset()
  vi.resetModules()
  for (const level of ['info', 'warn', 'error'] as const) vi.spyOn(console, level).mockImplementation(() => {})
})

const sentEvents = () =>
  apiRequest.mock.calls.flatMap(([, , body]) => (body as { entries: { event: string }[] }).entries.map((e) => e.event))

describe('uploadLogs', () => {
  it('sends queued entries once', async () => {
    const { logger } = await import('./log')
    const { uploadLogs } = await import('./logUpload')
    logger.info('app.start')
    await uploadLogs()
    await uploadLogs()

    expect(sentEvents()).toEqual(['app.start'])
  })

  it('keeps entries for the next upload when sending fails', async () => {
    const { logger } = await import('./log')
    const { uploadLogs } = await import('./logUpload')
    logger.info('app.start')
    apiRequest.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await uploadLogs()
    await uploadLogs()

    expect(sentEvents()).toEqual(['app.start', 'app.start', 'logs.upload_failed'])
  })

  it('drops a batch the API refuses as invalid instead of retrying it forever', async () => {
    const { logger } = await import('./log')
    const { uploadLogs } = await import('./logUpload')
    const { ApiError } = await import('./api/errors') // the copy logUpload uses
    logger.info('too.large')
    apiRequest.mockRejectedValueOnce(new ApiError(422, 'Log batches are limited to 1000000 bytes.'))
    await uploadLogs()
    logger.info('later')
    await uploadLogs()

    expect(sentEvents()).toEqual(['too.large', 'logs.batch_rejected', 'later'])
  })
})
