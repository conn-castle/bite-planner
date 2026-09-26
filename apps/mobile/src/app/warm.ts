import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { placedQuery } from '../features/orders/orders'

/**
 * Loads History's orders as soon as the app opens, so its tab never waits.
 * (Home, the first tab, loads its own.) A fresh cached copy isn't fetched again.
 */
export function useWarmCaches(): void {
  const queryClient = useQueryClient()
  useEffect(() => {
    void queryClient.prefetchQuery(placedQuery())
  }, [queryClient])
}
