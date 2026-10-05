import type { MealSummary } from '../../lib/api/types'

/** How long a meal takes: its total time, else its hands-on time; null when it gives neither. */
export function mealMinutes(meal: Pick<MealSummary, 'total_time_minutes' | 'active_time_minutes'>): number | null {
  return meal.total_time_minutes ?? meal.active_time_minutes
}

/** "25 min", "1 hr 10 min" */
export function formatMinutes(minutes: number): string {
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `${hours} hr ${rest} min` : `${hours} hr`
}
