import { describe, expect, it } from 'vitest'
import type { Meal, ProposedRecipe } from '../../lib/api/types'
import { diffLines, recipeDiff } from './recipeDiff'

const accepted: Meal = {
  id: 'chicken', revision: 0, name: 'Chicken', description: null, servings: 2,
  active_time_minutes: null, total_time_minutes: null, ingredient_count: 1, rating: null, picture: null,
  ingredients: [{ name: 'chicken thighs', quantity: 1, unit: 'lb', preparation: null }],
  steps: [{ text: 'Sear the chicken.', timer_minutes: null, ingredients: [0] }],
}
const proposal: ProposedRecipe = {
  name: 'Chicken', description: null, dish: 'plate', ingredient_count: 1,
  active_time_minutes: 10, total_time_minutes: 20,
  ingredients: [{ name: 'chicken breasts', quantity: 1, unit: 'lb', preparation: 'pounded thin', icon: 'drumstick', color: 'tan' }],
  steps: [{ text: 'Sear the chicken.', timer_minutes: 8, ingredients: ['chicken breasts'] }],
}

describe('full recipe review', () => {
  it('shows removals and additions with context across insertions and removals', () => {
    expect(diffLines(['a', 'b', 'c'], ['a', 'new', 'c', 'end'])).toEqual([
      { kind: 'same', text: 'a' }, { kind: 'removed', text: 'b' },
      { kind: 'added', text: 'new' }, { kind: 'same', text: 'c' }, { kind: 'added', text: 'end' },
    ])
  })
  it('shows ingredient replacement, preparation, missing facts, timers and ingredient assignments', () => {
    const [facts, ingredients, steps] = recipeDiff(accepted, proposal)
    expect(facts.lines).toContainEqual({ kind: 'removed', text: 'Total: not listed' })
    expect(facts.lines).toContainEqual({ kind: 'added', text: 'Total: 20 min' })
    expect(ingredients.lines).toEqual([
      { kind: 'removed', text: '1 lb chicken thighs' },
      { kind: 'added', text: '1 lb chicken breasts, pounded thin' },
    ])
    expect(steps.lines).toEqual([
      { kind: 'removed', text: 'Sear the chicken.\nTimer: none\nUses: chicken thighs' },
      { kind: 'added', text: 'Sear the chicken.\nTimer: 8 min\nUses: chicken breasts' },
    ])
  })
  it('shows timer-only and ingredient-assignment-only edits despite unchanged instruction text', () => {
    const withTimer = { ...accepted, steps: [{ ...accepted.steps[0], timer_minutes: 5 }] }
    const timerOnly = { ...proposal, ingredients: [{ ...proposal.ingredients[0], name: 'chicken thighs', preparation: null }], steps: [{ text: 'Sear the chicken.', timer_minutes: 10, ingredients: ['chicken thighs'] }] }
    const steps = recipeDiff(withTimer, timerOnly)[2].lines
    expect(steps.filter((l) => l.kind !== 'same').map((l) => l.text)).toEqual([
      'Sear the chicken.\nTimer: 5 min\nUses: chicken thighs',
      'Sear the chicken.\nTimer: 10 min\nUses: chicken thighs',
    ])
    expect(recipeDiff(accepted, { ...timerOnly, steps: [{ text: 'Sear the chicken.', timer_minutes: null, ingredients: [] }] })[2].lines[1]).toEqual({ kind: 'added', text: 'Sear the chicken.\nTimer: none\nUses: none' })
  })
})
