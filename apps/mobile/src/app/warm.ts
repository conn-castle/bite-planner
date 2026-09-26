import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { placedQuery } from '../features/orders/orders'
import { householdQuery } from '../features/settings/settings'

/**
 * Loads History's orders and the settings as soon as the app opens, so their
 * tabs never wait. (Home, the first tab, loads its own.) A fresh cached copy
 * isn't fetched again.
 */
export function useWarmCaches(): void {
  const queryClient = useQueryClient()
  useEffect(() => {
    void queryClient.prefetchQuery(placedQuery())
    void queryClient.prefetchQuery(householdQuery)
  }, [queryClient])
}
