import type { GroceryOrder, Job, MealSummary } from '../../lib/api/types'

/** An order's batches of suggestions (its `generate_meals` jobs) and the meals they made. */

type Batches = Pick<GroceryOrder, 'jobs'>

export const isRunning = (job: Job) => job.status === 'pending' || job.status === 'running'

/** Every suggestion, oldest batch first, so a new batch's meals appear below the ones already shown. */
export function suggestions({ jobs }: Batches): MealSummary[] {
  return jobs.filter((job) => job.kind === 'generate_meals').flatMap((job) => job.meals)
}

/** Whether a batch is still being made, so the order is worth checking again. */
export function hasRunningBatch({ jobs }: Batches): boolean {
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

/** The picked meals, in the order they were suggested. */
export function pickedMeals(order: Batches & Pick<GroceryOrder, 'meal_ids'>): MealSummary[] {
  const picked = new Set(order.meal_ids)
  return suggestions(order).filter((meal) => picked.has(meal.id))
}
