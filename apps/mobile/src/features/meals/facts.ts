import type { Meal } from '../../lib/api/types'

/** "25 min", "1 hr 10 min" */
export function formatMinutes(minutes: number): string {
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `${hours} hr ${rest} min` : `${hours} hr`
}

/** Short facts for a meal list: "15 min active · 30 min total · 8 ingredients". */
export function mealFacts(meal: Meal): string[] {
  const facts: string[] = []
  if (meal.active_time_minutes != null) facts.push(`${formatMinutes(meal.active_time_minutes)} active`)
  if (meal.total_time_minutes != null) facts.push(`${formatMinutes(meal.total_time_minutes)} total`)
  const count = meal.meal_ingredient.length
  facts.push(`${count} ${count === 1 ? 'ingredient' : 'ingredients'}`)
  return facts
}
