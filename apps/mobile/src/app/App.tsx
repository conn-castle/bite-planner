import { IonApp, setupIonicReact } from '@ionic/react'
import { IonReactRouter } from '@ionic/react-router'
import { defaultShouldDehydrateMutation } from '@tanstack/react-query'
import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client'
import { useEffect } from 'react'
import { NotInHousehold } from '../features/session/NotInHousehold'
import { useSession } from '../features/session/session'
import { SignInPage } from '../features/session/SignInPage'
import { Welcome } from '../features/session/Welcome'
import { useLogUpload } from '../lib/logUpload'
import { PERSIST_MAX_AGE_MS, persister, queryClient } from '../lib/query/queryClient'
import { Button } from '../ui/Button'
import { hideSplashScreen } from './native'
import { OfflineBanner } from './OfflineBanner'
import { Tabs } from './Tabs'

// react-router 7 behavior, opted into early (and its warnings silenced).
const ROUTER_FUTURE = { v7_startTransition: true, v7_relativeSplatPath: true }

// The iOS look everywhere, including in a desktop browser during development.
setupIonicReact({ mode: 'ios' })

export function App() {
  return (
    <PersistQueryClientProvider
      client={queryClient}
      persistOptions={{
        persister,
        maxAge: PERSIST_MAX_AGE_MS,
        // Dropped on every release, so data saved in an older shape is never restored. The cost: the first
        // launch after an update starts from an empty cache, and requests queued offline are dropped.
        // A local build's release is always `dev`, so local builds never drop it on their own.
        buster: __APP_RELEASE__,
        // Requests made offline are kept, except those only the open flow can send (e.g. taking the order's lock).
        dehydrateOptions: {
          shouldDehydrateMutation: (mutation) =>
            defaultShouldDehydrateMutation(mutation) && mutation.meta?.persist !== false,
        },
      }}
      // Send requests made offline before the last relaunch, then refresh.
      onSuccess={() =>
        void queryClient.resumePausedMutations().then(() => queryClient.invalidateQueries())
      }
    >
      <IonApp>
        <OfflineBanner />
        <SessionGate />
      </IonApp>
    </PersistQueryClientProvider>
  )
}

/** Decides between signing in, "not in a household", and the app itself. */
function SessionGate() {
  const { data: session, isPending, error, refetch, isFetching } = useSession()
  useLogUpload(Boolean(session?.household))

  // The native launch screen stays up until there is something real to show.
  useEffect(() => {
    if (!isPending) hideSplashScreen()
  }, [isPending])

  if (isPending) return null
  if (error && !session) {
    return (
      <Welcome title="Can't reach Castle Chef" subtitle={error.message}>
        <Button block onClick={() => void refetch()} disabled={isFetching}>
          Try again
        </Button>
      </Welcome>
    )
  }
  if (!session) return <SignInPage />
  if (!session.household) return <NotInHousehold session={session} />
  return (
    <IonReactRouter future={ROUTER_FUTURE}>
      <Tabs />
    </IonReactRouter>
  )
}
