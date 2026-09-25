import { Capacitor } from '@capacitor/core'
import { Haptics, ImpactStyle, NotificationType } from '@capacitor/haptics'
import { errorFields, logger } from './log'

/**
 * Haptic feedback for meaningful moments only (a committed action, a step
 * change). Native only: browsers have no equivalent worth using. A failure is
 * logged, never thrown, so it can't break the tap that caused it.
 */
function fire(name: string, feedback: () => Promise<void>) {
  if (!Capacitor.isNativePlatform()) return
  feedback().catch((error: unknown) => logger.warn('haptics.failed', { name, ...errorFields(error) }))
}

export const haptics = {
  tap: () => fire('tap', () => Haptics.impact({ style: ImpactStyle.Light })),
  success: () => fire('success', () => Haptics.notification({ type: NotificationType.Success })),
  error: () => fire('error', () => Haptics.notification({ type: NotificationType.Error })),
}
