import { type QueryClient, useQueryClient } from '@tanstack/react-query'
import type { GroceryOrder, MealSummary, NextOrder, PlacedOrder } from '../../lib/api/types'
import { queryKeys } from '../../lib/query/keys'
import { mealQuery } from './meals'

/**
 * A meal's summary from any list already cached (Home, History, a draft's
 * suggestions), so its page can show the name and facts at once while the
 * recipe loads.
 */
export function cachedMealSummary(queryClient: QueryClient, mealId: string): MealSummary | undefined {
  const lists: MealSummary[][] = [
    ...queryClient
      .getQueriesData<PlacedOrder[]>({ queryKey: queryKeys.placedOrders })
      .flatMap(([, orders]) => (orders ?? []).map((order) => order.meals)),
    ...queryClient
      .getQueriesData<GroceryOrder>({ queryKey: queryKeys.orders })
      .flatMap(([, order]) => (order?.jobs ?? []).map((job) => job.meals)),
    ...(queryClient.getQueryData<NextOrder>(queryKeys.nextOrder)?.draft?.jobs ?? []).map((job) => job.meals),
  ]
  return lists.flat().find((meal) => meal.id === mealId)
}

/** Starts loading a recipe before it's opened (e.g. as its row is pressed); a fresh cached one isn't fetched again. */
export function usePrefetchMeal(): (mealId: string) => void {
  const queryClient = useQueryClient()
  return (mealId) => void queryClient.prefetchQuery(mealQuery(mealId))
}
