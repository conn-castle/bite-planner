import type { GroceryOrder, RecipeChange } from '../../lib/api/types'

export function recipeBlockers(order: GroceryOrder): RecipeChange[] {
  const picked = new Set(order.picks.map((p) => p.meal_id))
  return order.recipe_changes.filter((c) => picked.has(c.meal_id))
}
export function unselectedChanges(order: GroceryOrder): RecipeChange[] {
  const picked = new Set(order.picks.map((p) => p.meal_id))
  return order.recipes_editable ? order.recipe_changes.filter((c) => !picked.has(c.meal_id)) : []
}


/** An acknowledgement applies only to this exact set of pending requests. */
export function unselectedChangeKey(order: GroceryOrder): string {
  return unselectedChanges(order).map((change) => change.job_id).sort().join(',')
}
