import { MealPage } from '../meals/MealPage'
import { useOrderSession } from './lock'
import { useOrder, useEditsPending } from './orders'
import { useToggleMeal } from './useToggleMeal'
import { AddMealButton } from '../../ui/AddMealButton'

/** Subscribes while the recipe is pushed; the grid and detail share saved picks. */
export function OrderMealPage({ orderId, mealId, servings }: { orderId: string; mealId: string; servings?: number }) {
  const session = useOrderSession()
  const order = useOrder(orderId, session)
  const selection = useToggleMeal(order.shown)
  const saving = useEditsPending()
  const summary = order.shown?.jobs.flatMap((j) => j.meals).find((m) => m.id === mealId)
  const action = summary && servings === undefined ? <AddMealButton iconOnly name={summary.name}
    added={order.shown?.picks.some((p) => p.meal_id === mealId) ?? false}
    disabled={summary.rating === 'not_for_us' || saving || order.shown?.lock?.session !== session}
    onToggle={() => void selection.toggle(summary)} /> : undefined
  return <MealPage selection={action} selectionProblem={selection.problem} mealId={mealId} servings={servings ?? null} picking={servings === undefined} order={order.shown} />
}
