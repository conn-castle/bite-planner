import { describe, expect, it } from 'vitest'
import type { Meal } from '../../lib/api/types'
import { formatMinutes, mealFacts } from './facts'

describe('formatMinutes', () => {
  it('uses minutes under an hour, hours and minutes above', () => {
    expect(formatMinutes(0)).toBe('0 min')
    expect(formatMinutes(45)).toBe('45 min')
    expect(formatMinutes(60)).toBe('1 hr')
    expect(formatMinutes(70)).toBe('1 hr 10 min')
  })
})

describe('mealFacts', () => {
  const meal: Meal = {
    id: 'm',
    name: 'Tacos',
    description: null,
    servings: 4,
    active_time_minutes: 15,
    total_time_minutes: 30,
    steps: ['Cook.'],
    created_at: '2026-09-24T12:00:00Z',
    meal_ingredient: [{ id: 'i', position: 0, name: 'tortillas', quantity: 8, unit: null, preparation: null }],
  }

  it('lists times and the ingredient count', () => {
    expect(mealFacts(meal)).toEqual(['15 min active', '30 min total', '1 ingredient'])
  })

  it('leaves out times the recipe does not give', () => {
    expect(mealFacts({ ...meal, active_time_minutes: null, total_time_minutes: null })).toEqual([
      '1 ingredient',
    ])
  })
})
