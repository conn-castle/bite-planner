import type { GroceryOrder, RecipeChange } from '../../lib/api/types'

export function recipeBlockers(order: GroceryOrder): RecipeChange[] {
  const picked = new Set(order.picks.map((p) => p.meal_id))
  return order.recipe_changes.filter((c) => picked.has(c.meal_id))
}
