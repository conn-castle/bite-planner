// Shapes returned by the Castle Chef API (backend/src/castle_chef/api.py).

/** A calendar date with no time of day or time zone: "2026-09-27". */
export type ISODate = string
/** A UTC timestamp: "2026-09-24T20:41:00Z". */
export type ISODateTime = string
/** A clock time with no date or time zone: "08:00:00". */
export type ClockTime = string

export interface HouseholdRef {
  id: string
  name: string
}

/** Settings of one member, just for them. */
export interface PersonalSettings {
  /** When their daily reminder goes out, in `time_zone`. */
  notification_time: ClockTime
  /** The phone's IANA zone; null until the app first reports it. */
  time_zone: string | null
  /** Whether they get the daily reminder to pick meals ("ideas ready" always goes out). */
  reminders_on: boolean
}

export interface Me {
  user_id: string
  email: string | null
  name: string | null
  /** Whether the account belongs to a household (`household` is set). */
  member: boolean
  household: HouseholdRef | null
  /** Null when not in a household. */
  settings: PersonalSettings | null
}

export interface HouseholdMember {
  user_id: string
  name: string | null
  /** For when there's no name. */
  email: string | null
}

/** The household's settings, shared by everyone in it (`GET /v1/household`). */
export interface HouseholdSettings {
  /** Days before an order's target date that it's decided by: 1–7. */
  decide_by_offset_days: number
  /** Days before the decide-by date that reminders start: 0–7 (0: on the decide-by day). */
  reminder_lead_days: number
  /** The servings each picked meal starts at: 1–12. */
  default_servings: number
}

export interface Household extends HouseholdSettings {
  id: string
  name: string
  /** In the order they were invited. */
  members: HouseholdMember[]
}

export interface Member {
  user_id: string
  name: string | null
}

export type JobStatus = 'pending' | 'running' | 'succeeded' | 'failed'

/**
 * The household's rating of a meal, worst to best: "don't show meals like
 * this" (the ✕ on a suggestion), thumbs down, thumbs up, and favorite (always liked).
 */
export type MealRating = 'not_for_us' | 'down' | 'up' | 'favorite'

/** A suggestion card: a meal without its recipe. */
export interface MealSummary {
  id: string
  name: string
  description: string | null
  active_time_minutes: number | null
  total_time_minutes: number | null
  /** Null when not rated. */
  rating: MealRating | null
}

/**
 * One run of the agent for an order: a batch of suggestions (20 meals), or
 * building its shopping list.
 */
export interface Job {
  id: string
  kind: 'generate_meals' | 'build_list'
  status: JobStatus
  error: string | null
  requested_by: Member
  created_at: ISODateTime
  /** A batch's meals: empty until it succeeds (and for a list); in a fixed order. */
  meals: MealSummary[]
}

/** Where an item is in the store, in the store's order. */
export type Aisle = 'produce' | 'meat_seafood' | 'dairy_eggs' | 'frozen' | 'pantry' | 'other'

/** An item of an order's shopping list. */
export interface ShoppingItem {
  id: string
  name: string
  /** What's ordered, in `unit` (a count when null): the member's amount, or the recipes'; null when they give none. */
  quantity: number | null
  /** The amount worked out for the recipes, which the member can go back to. */
  recipe_quantity: number | null
  unit: string | null
  aisle: Aisle
  /** The picked meals it's for. */
  meal_ids: string[]
  /** Assumed on hand (salt, pepper, ...): never ordered, shown only on request. */
  on_hand: boolean
  removed: boolean
}

/** A draft's shopping list: its newest build, and the newest succeeded build's items. */
export interface ShoppingList {
  job: Job
  /** Ready for the order's picks and servings now: the newest build succeeded, from them. */
  current: boolean
  /** By aisle; empty until a build succeeds. */
  items: ShoppingItem[]
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
  /** The target date less the household's decide-by offset. */
  decide_by_date: ISODate | null
  /** The decide-by date less the household's reminder lead: daily reminders from then. */
  reminders_start_date: ISODate | null
  earliest_delivery_date: ISODate
  delivery_date: ISODate | null
  next_delivery_date: ISODate | null
  /** Its batches of suggestions, oldest first; the suggestions are their meals. */
  jobs: Job[]
  /** In the order their meals were suggested. */
  picks: MealPick[]
  /** Who is placing it (a draft's live lock), or null when no one is. */
  lock: OrderLock | null
  /** Its shopping list; null until one is started. */
  shopping_list: ShoppingList | null
}

/** The create-order flow's steps, as a lock reports where its holder is. */
export type OrderStep = 'pick' | 'servings' | 'delivery' | 'next' | 'list' | 'review'

/**
 * The order lock: one ordering session (an opening of the flow, on one phone)
 * has it until the flow closes, the order is placed, or 2 minutes pass without
 * a renewal.
 */
export interface OrderLock {
  member: Member
  /** The session that has it; only its own member sees it (null for everyone else). */
  session: string | null
  /** When they took it. */
  since: ISODateTime
  step: OrderStep
  expires_at: ISODateTime
}

/** A meal picked for an order, and how many to make of it (the recipe is for `Meal.servings`). */
export interface MealPick {
  meal_id: string
  servings: number
}

/** A placed order (`GET /v1/grocery-orders`): it can't change, so it has no suggestions, and its dates are set. */
export interface PlacedOrder {
  id: string
  number: number
  placed_at: ISODateTime
  placed_by: Member
  delivery_date: ISODate
  /** Its meals cover the days up to the one before this. */
  next_delivery_date: ISODate
  /** The picked meals, in the order they were suggested. */
  meals: PlacedMeal[]
  /** What was ordered for them, by aisle. */
  shopping_list: ShoppingItem[]
}

/** A meal of a placed order, and how many were ordered of it. */
export interface PlacedMeal extends MealSummary {
  servings: number
}

/** The household's next order (`GET /v1/grocery-orders/next`), whether or not anyone has started it. */
export interface NextOrder {
  /** The number it has, or will have once started. */
  number: number
  /** The last placed order's next delivery date; null before the first order. */
  target_date: ISODate | null
  decide_by_date: ISODate | null
  /** The first day of the daily reminders to decide it (they go on until it's placed); null without a target date. */
  reminders_start_date: ISODate | null
  earliest_delivery_date: ISODate
  /** Its draft, once someone has started one. */
  draft: GroceryOrder | null
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
