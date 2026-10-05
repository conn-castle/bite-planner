import type { GroceryOrder, Job, MealSummary } from '../../lib/api/types'

/**
 * An order's batches of suggestions (its `generate_meals` jobs) and the meals
 * they made, and whether any of its jobs is still running.
 */

type Batches = Pick<GroceryOrder, 'jobs'>

export const isRunning = (job: Job) => job.status === 'pending' || job.status === 'running'

/** Every suggestion, oldest batch first, so a new batch's meals appear below the ones already shown. */
export function suggestions({ jobs }: Batches): MealSummary[] {
  return jobs.filter((job) => job.kind === 'generate_meals').flatMap((job) => job.meals)
}

/**
 * Whether any of the order's jobs is still running, so the order is worth
 * checking again: a batch of suggestions, or its list being built or shopped,
 * or a quote. This is what keeps the order polled while the list and review
 * steps wait, so it counts every kind of job, not just batches.
 */
export function hasRunningJob({ jobs }: Batches): boolean {
  return jobs.some(isRunning)
}

/**
 * What the newest batch means for the screen: still being made, failed (with
 * a way to try again), or done. Only the newest counts: an older failure that
 * a later batch replaced is no longer news.
 */
export type BatchState = { status: 'none' } | { status: 'running'; job: Job } | { status: 'failed'; job: Job } | { status: 'done' }

export function newestBatchState({ jobs }: Batches): BatchState {
  const newest = jobs.filter((job) => job.kind === 'generate_meals').at(-1)
  if (!newest) return { status: 'none' }
  if (isRunning(newest)) return { status: 'running', job: newest }
  if (newest.status === 'failed') return { status: 'failed', job: newest }
  return { status: 'done' }
}

/** A picked meal, and how many to make of it. */
export interface PickedMeal {
  meal: MealSummary
  servings: number
}

/** The picked meals, in the order they were suggested. */
export function pickedMeals(order: Batches & Pick<GroceryOrder, 'picks'>): PickedMeal[] {
  const servings = new Map(order.picks.map((pick) => [pick.meal_id, pick.servings]))
  return suggestions(order).flatMap((meal) => {
    const count = servings.get(meal.id)
    return count === undefined ? [] : [{ meal, servings: count }]
  })
}
