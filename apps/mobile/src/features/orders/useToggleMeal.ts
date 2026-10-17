import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { GroceryOrder, MealSummary } from '../../lib/api/types'
import { errorMessage } from '../../lib/errorMessage'
import { haptics } from '../../lib/haptics'
import { errorFields, logger } from '../../lib/log'
import { householdQuery } from '../settings/settings'
import { useOrderSession } from './lock'
import { useSetMeal } from './orders'

/** Card and detail share the same selection save and household default. */
export function useToggleMeal(order: GroceryOrder | undefined) {
  const session = useOrderSession()
  const queryClient = useQueryClient()
  const setMeal = useSetMeal()
  const [problem, setProblem] = useState<string | null>(null)
  async function toggle(meal: MealSummary) {
    if (!order) return
    haptics.tap()
    setProblem(null)
    if (order.picks.some((pick) => pick.meal_id === meal.id)) {
      setMeal.mutate({ kind: 'meal', orderId: order.id, mealId: meal.id, picked: false, session })
      return
    }
    try {
      const servings = (await queryClient.ensureQueryData(householdQuery)).default_servings
      setMeal.mutate({ kind: 'meal', orderId: order.id, mealId: meal.id, picked: true, servings, session })
    } catch (error) {
      logger.warn('orders.pick_needs_household', errorFields(error))
      haptics.error()
      setProblem(errorMessage(error))
    }
  }
  return { toggle, problem: problem ?? setMeal.error?.message ?? null, mutation: setMeal }
}
