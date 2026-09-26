import { Capacitor } from '@capacitor/core'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

/* Ionic: core styles, then the basics the app builds on. */
import '@ionic/react/css/core.css'
import '@ionic/react/css/normalize.css'
import '@ionic/react/css/structure.css'
import '@ionic/react/css/typography.css'

// Inter for devices without SF Pro, with optical sizes for large titles.
import '@fontsource-variable/inter/opsz.css'

import './theme/tokens.css'
import './theme/app.css'

import { App } from './app/App'
import { ErrorBoundary } from './app/ErrorBoundary'
import { registerRatingMutations } from './features/meals/ratings'
import { registerOrderMutations } from './features/orders/orders'
import { logger, logUncaughtErrors } from './lib/log'
import { queryClient } from './lib/query/queryClient'

logUncaughtErrors()
logger.info('app.start', {
  version: __APP_VERSION__,
  release: __APP_RELEASE__,
  platform: Capacitor.getPlatform(),
})
registerOrderMutations(queryClient)
registerRatingMutations(queryClient)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
