import { describe, expect, it } from 'vitest'
import type { Job, JobStatus, MealSummary } from '../../lib/api/types'
import { hasRunningJob, newestBatchState, pickedMeals, suggestions } from './batches'

const meal = (id: string): MealSummary => ({
  id,
  name: id,
  description: null,
  active_time_minutes: null,
  total_time_minutes: null,
  rating: null,
})

const batch = (id: string, status: JobStatus, mealIds: string[] = []): Job => ({
  id,
  kind: 'generate_meals',
  status,
  error: status === 'failed' ? 'The agent timed out.' : null,
  requested_by: { user_id: 'u1', name: 'Sam' },
  created_at: '2026-09-24T20:41:00Z',
  meals: mealIds.map(meal),
})

describe('suggestions', () => {
  it('lists the meals of every batch, oldest batch first, so new ones appear below', () => {
    const jobs = [batch('first', 'succeeded', ['a', 'b']), batch('more', 'succeeded', ['c'])]
    expect(suggestions({ jobs }).map((m) => m.id)).toEqual(['a', 'b', 'c'])
  })

  it('keeps earlier suggestions when a later batch fails or is still running', () => {
    const jobs = [batch('first', 'succeeded', ['a']), batch('failed', 'failed'), batch('more', 'running')]
    expect(suggestions({ jobs }).map((m) => m.id)).toEqual(['a'])
  })

  it('is empty before the first batch finishes', () => {
    expect(suggestions({ jobs: [batch('first', 'pending')] })).toEqual([])
  })
})

describe('hasRunningJob', () => {
  it('is true while a batch is waiting to start or being made', () => {
    expect(hasRunningJob({ jobs: [batch('a', 'pending')] })).toBe(true)
    expect(hasRunningJob({ jobs: [batch('a', 'succeeded'), batch('b', 'running')] })).toBe(true)
  })

  it('is true while the list is built or shopped, or a quote is read, so the order is checked until they finish', () => {
    for (const kind of ['build_list', 'shop', 'quote'] as const) {
      expect(hasRunningJob({ jobs: [batch('a', 'succeeded'), { ...batch(kind, 'running'), kind }] })).toBe(true)
    }
  })

  it('is false once every job has finished, whether it worked or not', () => {
    expect(hasRunningJob({ jobs: [batch('a', 'succeeded'), batch('b', 'failed')] })).toBe(false)
    expect(hasRunningJob({ jobs: [] })).toBe(false)
  })
})

describe('newestBatchState', () => {
  it('reports no batch for a new draft', () => {
    expect(newestBatchState({ jobs: [] })).toEqual({ status: 'none' })
  })

  it('reports the newest batch while it is being made', () => {
    const running = batch('more', 'running')
    expect(newestBatchState({ jobs: [batch('first', 'succeeded', ['a']), running] })).toEqual({
      status: 'running',
      job: running,
    })
  })

  it('reports a failure only while it is the newest batch', () => {
    const failed = batch('more', 'failed')
    expect(newestBatchState({ jobs: [batch('first', 'succeeded', ['a']), failed] })).toEqual({
      status: 'failed',
      job: failed,
    })
    expect(newestBatchState({ jobs: [failed, batch('retry', 'succeeded', ['b'])] })).toEqual({ status: 'done' })
  })
})

describe('pickedMeals', () => {
  it('lists the picks in the order they were suggested, not the order they were picked', () => {
    const jobs = [batch('first', 'succeeded', ['a', 'b', 'c'])]
    const picks = [
      { meal_id: 'c', servings: 2 },
      { meal_id: 'a', servings: 6 },
    ]
    expect(pickedMeals({ jobs, picks }).map(({ meal, servings }) => [meal.id, servings])).toEqual([
      ['a', 6],
      ['c', 2],
    ])
  })
})
