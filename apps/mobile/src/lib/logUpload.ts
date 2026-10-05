import { App } from '@capacitor/app'
import { Capacitor } from '@capacitor/core'
import { Device } from '@capacitor/device'
import { onlineManager } from '@tanstack/react-query'
import { useEffect } from 'react'
import { apiRequest } from './api/client'
import { ApiError } from './api/errors'
import type { LogsIn } from './api/types'
import { errorFields, flushLogs, logger, nextLogBatch } from './log'

/**
 * Uploads the app's logs to the API, which writes them to CloudWatch next to
 * the backend's: every minute while the app is open, and as soon as it goes to
 * the background. Entries stay in the outbox until the API has them.
 */

const INTERVAL_MS = 60_000
// Well under the API's limits (2000 entries, 1 MB per batch).
const BATCH_SIZE = 200

let uploading: Promise<void> | undefined

async function deviceInfo() {
  const info = await Device.getInfo()
  return {
    platform: Capacitor.getPlatform(),
    model: info.model,
    os: `${info.operatingSystem} ${info.osVersion}`,
    webView: info.webViewVersion,
    appVersion: __APP_VERSION__,
    appRelease: __APP_RELEASE__,
    userAgent: navigator.userAgent,
  }
}

async function uploadPending() {
  // Offline, the outbox keeps everything until the next upload.
  if (!onlineManager.isOnline()) return
  const device = await deviceInfo()
  for (;;) {
    const batch = await nextLogBatch(BATCH_SIZE)
    if (batch.entries.length === 0) return
    try {
      await apiRequest<void>('POST', '/v1/logs', { device, entries: batch.entries } satisfies LogsIn)
    } catch (error) {
      // A batch the API refuses as invalid (e.g. over 1 MB) would be refused
      // forever and block every later one: drop it and say so.
      if (!(error instanceof ApiError && (error.status === 413 || error.status === 422))) throw error
      logger.warn('logs.batch_rejected', { entries: batch.entries.length, ...errorFields(error) })
    }
    batch.commit()
  }
}

/** Uploads what is waiting; a call while an upload runs joins it. */
export function uploadLogs(): Promise<void> {
  uploading ??= uploadPending()
    .catch((error: unknown) => logger.warn('logs.upload_failed', errorFields(error)))
    .finally(() => {
      uploading = undefined
    })
  return uploading
}

/** Uploads logs while `enabled` (the API accepts them only from household members). */
export function useLogUpload(enabled: boolean) {
  useEffect(() => {
    if (!enabled) return
    void uploadLogs()
    const timer = setInterval(() => void uploadLogs(), INTERVAL_MS)
    const listener = App.addListener('appStateChange', ({ isActive }) => {
      if (isActive) return
      flushLogs()
      void uploadLogs()
    })
    return () => {
      clearInterval(timer)
      void listener.then((handle) => handle.remove())
    }
  }, [enabled])
}
