import { SplashScreen } from '@capacitor/splash-screen'
import { errorFields, logger } from '../lib/log'

/** Hides the native launch screen; called once the first real screen has rendered. */
export function hideSplashScreen() {
  SplashScreen.hide({ fadeOutDuration: 250 }).catch((error: unknown) =>
    logger.error('splash.hide_failed', errorFields(error)),
  )
}
