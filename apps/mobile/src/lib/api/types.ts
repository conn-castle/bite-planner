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

/** What booking does when an approved product is gone: use its first available alternative, or ask. */
export type ProductGone = 'use_alternative' | 'ask'

/** The household's settings, shared by everyone in it (`GET /v1/household`). */
export interface HouseholdSettings {
  /** Days before an order's target date that it's decided by: 1–7. */
  decide_by_offset_days: number
  /** Days before the decide-by date that reminders start: 0–7 (0: on the decide-by day). */
  reminder_lead_days: number
  /** The servings each picked meal starts at: 1–12. */
  default_servings: number
  /** The latest time deliveries should arrive by (each order's starts at it), in the store's time zone. */
  delivery_time: ClockTime
  /** The tip: this percentage of the items' total (0–30), never more than `tip_max_cents` (up to $100). */
  tip_percent: number
  tip_max_cents: number
  product_gone: ProductGone
}

export interface Household extends HouseholdSettings {
  id: string
  name: string
  /** In the order they were invited. */
  members: HouseholdMember[]
  /** Invitations not used yet, oldest first. */
  invites: HouseholdInvite[]
}

/** Someone invited, who joins when they first sign in with this email. */
export interface HouseholdInvite {
  id: string
  name: string
  email: string
  /** The member who invited them; null for the admin command. */
  invited_by: Member | null
  created_at: ISODateTime
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
  kind: 'generate_meals' | 'build_list' | 'shop' | 'quote' | 'book'
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
/** A product at the store, and how many of it: packages, bags, or pieces. */
export interface Product {
  product_id: string
  name: string
  /** As the store labels it: "2 lb", "13.5 fl oz", or "per lb" for weighed items. */
  size: string | null
  /** One's price in cents (estimated for weighed items); null if the store shows none. */
  price_cents: number | null
  /** Weighed items: the price per pound, and a package's estimated weight. */
  price_per_lb_cents: number | null
  estimated_lb: number | null
  image_url: string | null
  bought_before: boolean
  count: number
}

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
  /** Taken off the list, or its product not to be bought. */
  removed: boolean
  /** What's bought for it at the store, once the list is shopped; null for an item the store doesn't have until the member picks an alternative. */
  product: Product | null
  /** What the member can switch to (up to 8), or why there are none. */
  alternatives: Product[]
  no_alternatives_reason: string | null
  /** The store doesn't have it; why the meals work without it, if they do. */
  not_found: boolean
  skippable_reason: string | null
}

/** A draft's shopping list: its newest build, and the newest succeeded build's items. */
export interface ShoppingList {
  job: Job
  /** Ready for the order's picks and servings now: the newest build succeeded, from them. */
  current: boolean
  /** Shopping the list at the store: its newest shop job (null until it starts). */
  shop: Job | null
  /** The store it's shopped at: "Wegmans". */
  store: string
  /** By aisle; empty until a build succeeds. */
  items: ShoppingItem[]
}

/** One line of checkout's summary, as Instacart labels it: "Service fee", 249. */
export interface QuoteLine {
  label: string
  cents: number
}

/** What checkout showed for the order's products, with a scheduled delivery and the household's tip. */
export interface Checkout {
  /** The items' total, with the store's loyalty prices. */
  items_cents: number
  /** The summary's other lines (delivery, service fee, tax, ...). */
  fees: QuoteLine[]
  tip_cents: number
  total_cents: number
  /** The account's delivery address, to its ZIP code. */
  address: string
  /** "Visa ending 4242" */
  card: string
  /** What Instacart does when an item is out of stock, per item: the different settings ("Replace with best match"). */
  replacements: string[]
}

/** What the order would cost at the store (the final review): its newest quote. */
export interface Quote {
  job: Job
  /** For the order's products, counts, and tip rule now; otherwise a new one is due. */
  current: boolean
  /** It failed because Castle Chef is signed out of Instacart's website (someone signs it in again first). */
  signed_out: boolean
  /** Once it succeeds: why Instacart won't check the cart out (below the store's minimum), or checkout. */
  blocked_because: string | null
  checkout: Checkout | null
}

/** A delivery window, in the store's time zone. */
export interface DeliveryWindow {
  start: ClockTime
  end: ClockTime
}

/**
 * Where a placed (approved) order is with the store: waiting for the day it
 * books (four days before delivery), being booked, booked, or not booked and why
 * (a time or a product to choose, a failure before Place order, or Place order
 * pressed without Instacart confirming it).
 */
export type BookState =
  | 'waiting'
  | 'booking'
  | 'booked'
  | 'needs_time'
  | 'needs_product'
  | 'not_booked'
  | 'check_instacart'

/** A product gone when the order booked, and the alternative bought instead. */
export interface Swap {
  /** The list's item: "yellow onions". */
  item: string
  gone: string
  bought: string
}

/** What Instacart confirmed for a booked order; each unknown (null) for one settled by hand ("It was placed"). */
export interface Booking {
  /** Instacart's order number; "Dry run" for development's dry run. */
  order_number: string | null
  window: DeliveryWindow | null
  /** What was charged. */
  total_cents: number | null
  swaps: Swap[]
}

/** Why an approved order isn't booked, as whoever approved it is told; for a product gone, the list items to choose again. */
export interface BookIssue {
  message: string
  item_ids: string[]
}

/** Where a placed order is with the store (null for a draft): GroceryOrder's and PlacedOrder's. */
export interface BookFields {
  book_state: BookState | null
  /** The day it books. */
  book_on: ISODate | null
  /** Once booked: null for orders placed before booking existed. */
  booking: Booking | null
  book_issue: BookIssue | null
}

export interface GroceryOrder extends BookFields {
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
  /** The latest time it should arrive by, in the store's time zone; null until chosen (the household's delivery time shows). */
  delivery_by: ClockTime | null
  /** For that time: the window booking picks when every window is open; null when none ends by it or includes it (it can't be booked). */
  best_window: DeliveryWindow | null
  /** The one window booking may use that ends after that time, if nothing ending by it is left ("or 2–4 PM"). */
  fallback_window: DeliveryWindow | null
  next_delivery_date: ISODate | null
  /** The furthest step its flow has reached: where the flow opens again. */
  reached_step: OrderStep
  /** Its batches of suggestions, oldest first; the suggestions are their meals. */
  jobs: Job[]
  /** In the order their meals were suggested. */
  picks: MealPick[]
  /** Who is placing it (a draft's live lock), or null when no one is. */
  lock: OrderLock | null
  /** Its shopping list; null until one is started. */
  shopping_list: ShoppingList | null
  /** What it would cost: its newest quote; null until one is started. */
  quote: Quote | null
}

/** The create-order flow's steps, in order: where a lock's holder is, and how far an order's flow has gone. */
export type OrderStep = 'pick' | 'servings' | 'list' | 'delivery' | 'next' | 'review'

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
export interface PlacedOrder extends BookFields {
  book_state: BookState
  book_on: ISODate
  id: string
  number: number
  placed_at: ISODateTime
  placed_by: Member
  delivery_date: ISODate
  /** The latest time it should arrive by; null for orders placed before there was one. */
  delivery_by: ClockTime | null
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

/** Body of `PUT /v1/grocery-orders/{id}/dates`: both dates and the latest time, every time. */
export interface OrderDates {
  delivery_date: ISODate | null
  delivery_by: ClockTime | null
  next_delivery_date: ISODate | null
}

export interface MealIngredient {
  name: string
  quantity: number | null
  unit: string | null
  preparation: string | null
}

/** The full recipe. */
export interface MealStep {
  text: string
  /** How long the cook waits in it, for a timer; null when there's nothing to time. */
  timer_minutes: number | null
  /** The positions in the meal's `ingredients` of the ones it uses. */
  ingredients: number[]
}

export interface Meal extends MealSummary {
  servings: number
  steps: MealStep[]
  ingredients: MealIngredient[]
}
