import { useEffect } from 'react'
import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client'
import { createHashRouter, RouterProvider, useRouteError } from 'react-router'
import { AccountPage } from './AccountPage'
import { CrashScreen } from './ErrorBoundary'
import { Layout } from './Layout'
import { errorFields, logger } from './log'
import { PERSIST_MAX_AGE_MS, persister, queryClient } from './queryClient'
import { RequestPage } from './RequestPage'
import { RequestsPage } from './RequestsPage'

function RouteError() {
  const error = useRouteError()
  useEffect(() => logger.error('route.crashed', errorFields(error)), [error])
  return <CrashScreen />
}

// Hash URLs work from any origin, including the app's capacitor:// origin.
const router = createHashRouter([
  {
    element: <Layout />,
    errorElement: <RouteError />,
    children: [
      { path: '/', element: <RequestsPage /> },
      { path: '/requests/:jobId', element: <RequestPage /> },
      { path: '/account', element: <AccountPage /> },
    ],
  },
])

router.subscribe((state) => logger.info('route.changed', { path: state.location.pathname }))

export default function App() {
  return (
    <PersistQueryClientProvider
      client={queryClient}
      persistOptions={{ persister, maxAge: PERSIST_MAX_AGE_MS, buster: __APP_VERSION__ }}
      // Send requests made offline before the last relaunch, then refresh.
      onSuccess={() =>
        void queryClient.resumePausedMutations().then(() => queryClient.invalidateQueries())
      }
    >
      <RouterProvider router={router} />
    </PersistQueryClientProvider>
  )
}
