import { Capacitor } from '@capacitor/core'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { ErrorBoundary } from './ErrorBoundary'
import './index.css'
import { logger, logUncaughtErrors } from './log'

logUncaughtErrors()
logger.info('app.start', { version: __APP_VERSION__, platform: Capacitor.getPlatform() })

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
