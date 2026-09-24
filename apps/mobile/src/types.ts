// Shapes returned by the Castle Chef API (backend/src/castle_chef/api.py).

export type JobStatus = 'pending' | 'running' | 'succeeded' | 'failed'

export interface Job {
  id: string
  kind: string
  params: { count: number }
  status: JobStatus
  error: string | null
  created_at: string
  finished_at: string | null
}

export interface MealIngredient {
  id: string
  position: number
  name: string
  quantity: number | null
  unit: string | null
  preparation: string | null
}

export interface Meal {
  id: string
  name: string
  description: string | null
  servings: number
  active_time_minutes: number | null
  total_time_minutes: number | null
  steps: string[]
  created_at: string
  meal_ingredient: MealIngredient[]
}
