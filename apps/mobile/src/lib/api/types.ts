// Shapes returned by the Castle Chef API (backend/src/castle_chef/api.py).

export interface Household {
  id: string
  name: string
}

export interface Me {
  user_id: string
  email: string | null
  name: string | null
  /** Whether the account belongs to a household (`household` is set). */
  member: boolean
  household: Household | null
}

export type JobStatus = 'pending' | 'running' | 'succeeded' | 'failed'

/** A meal request: the agent generating `params.count` meals. */
export interface Job {
  id: string
  kind: string
  params: { count: number }
  status: JobStatus
  error: string | null
  created_at: string
  finished_at: string | null
  /** The meals it produced, in order (empty until it succeeds). */
  meals: MealSummary[]
}

export interface MealSummary {
  id: string
  name: string
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
