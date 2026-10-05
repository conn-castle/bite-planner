import { App } from '@capacitor/app'
import { Network } from '@capacitor/network'
import { Preferences } from '@capacitor/preferences'
import { createAsyncStoragePersister } from '@tanstack/query-async-storage-persister'
import {
  focusManager,
  MutationCache,
  onlineManager,
  QueryCache,
  QueryClient,
} from '@tanstack/react-query'
import { errorFields, logger } from '../log'

declare module '@tanstack/react-query' {
  interface Register {
    // `persist: false`: a request never saved to be sent after a relaunch (see App.tsx).
    mutationMeta: { persist?: boolean }
  }
}

/**
 * Server state lives in TanStack Query: cached, shown instantly (including
 * after a relaunch, via the persisted cache), and refreshed in the background.
 */

// How long cached data may be restored after the app was closed.
export const PERSIST_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000

export const queryClient = new QueryClient({
  queryCache: new QueryCache({
    // Warnings: offline or a refused request is expected; the API logs its own errors.
    onError: (error, query) =>
      logger.warn('query.failed', { queryKey: query.queryKey, ...errorFields(error) }),
  }),
  mutationCache: new MutationCache({
    onError: (error, _variables, _context, mutation) =>
      logger.warn('mutation.failed', {
        mutationKey: mutation.options.mutationKey,
        ...errorFields(error),
      }),
    onSuccess: (_data, _variables, _context, mutation) =>
      logger.info('mutation.succeeded', { mutationKey: mutation.options.mutationKey }),
  }),
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      // Must outlive the persisted cache, or restored data is dropped at once.
      gcTime: PERSIST_MAX_AGE_MS,
      retry: 2,
    },
  },
})

// Capacitor Preferences: UserDefaults on iOS (not evicted like WebView storage),
// localStorage in a browser.
export const persister = createAsyncStoragePersister({
  key: 'castle-chef.query-cache',
  throttleTime: 1000,
  storage: {
    getItem: async (key) => (await Preferences.get({ key })).value,
    setItem: (key, value) => Preferences.set({ key, value }),
    removeItem: (key) => Preferences.remove({ key }),
  },
})

// Refetch stale data when the app returns to the foreground.
focusManager.setEventListener((setFocused) => {
  const listener = App.addListener('appStateChange', ({ isActive }) => {
    logger.info('app.state', { active: isActive })
    setFocused(isActive)
  })
  return () => void listener.then((handle) => handle.remove())
})

// Pause queries and mutations while offline; resume when back online.
onlineManager.setEventListener((setOnline) => {
  void Network.getStatus().then((status) => setOnline(status.connected))
  const listener = Network.addListener('networkStatusChange', (status) => {
    logger.info('network.status', { connected: status.connected, type: status.connectionType })
    setOnline(status.connected)
  })
  return () => void listener.then((handle) => handle.remove())
})
