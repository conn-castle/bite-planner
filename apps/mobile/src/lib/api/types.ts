// Shapes returned by the Castle Chef API (backend/src/castle_chef/api.py).

/** A calendar date with no time of day or time zone: "2026-09-27". */
export type ISODate = string
/** A UTC timestamp: "2026-09-24T20:41:00Z". */
export type ISODateTime = string

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

export interface Member {
  user_id: string
  name: string | null
}

export type JobStatus = 'pending' | 'running' | 'succeeded' | 'failed'

/** A suggestion card: a meal without its recipe. */
export interface MealSummary {
  id: string
  name: string
  description: string | null
  active_time_minutes: number | null
  total_time_minutes: number | null
}

/** One batch of suggestions: the agent generating 20 meals for an order. */
export interface Job {
  id: string
  kind: 'generate_meals'
  status: JobStatus
  error: string | null
  requested_by: Member
  created_at: ISODateTime
  /** Empty until it succeeds; in a fixed order. */
  meals: MealSummary[]
}

export interface GroceryOrder {
  id: string
  /** "Order 3" */
  number: number
  status: 'draft' | 'placed'
  created_by: Member
  created_at: ISODateTime
  /** The previous order's next delivery date; null for the first order. */
  target_date: ISODate | null
  earliest_delivery_date: ISODate
  delivery_date: ISODate | null
  next_delivery_date: ISODate | null
  /** Its batches of suggestions, oldest first; the suggestions are their meals. */
  jobs: Job[]
  /** The picks. */
  meal_ids: string[]
}

/** Body of `PUT /v1/grocery-orders/{id}/dates`: both dates, every time. */
export interface OrderDates {
  delivery_date: ISODate | null
  next_delivery_date: ISODate | null
}

export interface MealIngredient {
  name: string
  quantity: number | null
  unit: string | null
  preparation: string | null
}

/** The full recipe. */
export interface Meal extends MealSummary {
  servings: number
  steps: string[]
  ingredients: MealIngredient[]
}
