import type { Meal } from '../../lib/api/types'
import { describe, expect, it } from 'vitest'
import { confirmedIngredientAmount, effectiveRecipe, formatQuantity, formatUnit, ingredientAmount, scaleIngredient, scaleQuantity } from './servings'

describe('scaleQuantity', () => {
  it('scales proportionally to the servings picked', () => {
    expect(scaleQuantity(1.5, 4, 6)).toBe(2.25)
    expect(scaleQuantity(3, 6, 2)).toBe(1)
  })
})

describe('formatQuantity', () => {
  it.each([
    [21, '21'],
    [0.5, '½'],
    [0.25, '¼'],
    [0.375, '⅜'],
    [2.25, '2¼'],
    [1.5, '1½'],
    [1 / 3, '⅓'],
    [4 / 3, '1⅓'],
    [0.66, '⅔'],
    [2.98, '3'],
    [3.01, '3'],
  ])('shows %s as %s', (quantity, expected) => {
    expect(formatQuantity(quantity)).toBe(expected)
  })

  it('falls back to decimals when no kitchen fraction is close', () => {
    expect(formatQuantity(0.2)).toBe('0.2')
  })

  it('shows amounts below ⅛ as "<⅛"', () => {
    expect(formatQuantity(0.0625)).toBe('<⅛')
  })
})

describe('scaleIngredient', () => {
  it('keeps measured ingredients exact', () => {
    expect(scaleIngredient(3, 'clove', 4, 5)).toBe(3.75)
  })

  it('rounds counted ingredients to the nearest half, never below one half', () => {
    expect(scaleIngredient(3, null, 4, 5)).toBe(4)
    expect(scaleIngredient(3, null, 4, 3)).toBe(2.5)
    expect(scaleIngredient(1, null, 6, 1)).toBe(0.5)
  })
})

describe('formatUnit', () => {
  it('matches countable units to the quantity', () => {
    expect(formatUnit('cups', 1)).toBe('cup')
    expect(formatUnit('cup', 1.5)).toBe('cups')
    expect(formatUnit('Cloves', 0.5)).toBe('clove')
  })

  it('leaves abbreviations and unknown units unchanged', () => {
    expect(formatUnit('tbsp', 3)).toBe('tbsp')
    expect(formatUnit('lb', 2)).toBe('lb')
    expect(formatUnit('handful', 2)).toBe('handful')
  })
})

describe('ingredientAmount', () => {
  const ingredient = { name: 'chicken thighs', quantity: 1.5, unit: 'lb', preparation: null }

  it('is the amount for the servings being cooked', () => {
    expect(ingredientAmount(ingredient, 4, 6)).toBe('2¼ lb')
    expect(ingredientAmount({ ...ingredient, name: 'eggs', quantity: 3, unit: null }, 4, 2)).toBe('1½')
  })

  it('is empty when the recipe gives none', () => {
    expect(ingredientAmount({ ...ingredient, name: 'salt', quantity: null, unit: null }, 4, 6)).toBe('')
  })
})

describe('confirmed order recipe and local previews', () => {
  const base: Meal = {
    id: 'beans', name: 'Beans', description: null, revision: 3, servings: 2,
    active_time_minutes: 10, total_time_minutes: 20, ingredient_count: 2, rating: null, picture: null,
    ingredients: [{ name: 'beans', quantity: .75, unit: 'can', preparation: 'drained' },
      { name: 'lemon', quantity: .25, unit: null, preparation: null }],
    steps: [{ text: 'Heat ½ can; reserve ¼. Add ¼ lemon.', ingredients: [0, 1], timer_minutes: 7 }],
    prepared: { build_id: 'build-5', base_revision: 3, servings: 5, legacy_scaled: false,
      ingredients: [{ name: 'beans', quantity: 2, unit: 'can', preparation: 'drained' },
        { name: 'lemon', quantity: .25, unit: null, preparation: null }],
      steps: [{ text: 'Heat 1½ cans; reserve ½. Add ¼ lemon.', ingredients: [0, 1], timer_minutes: 7 }] },
  }
  it('shows confirmed consumption unchanged and resets from a base-derived preview', () => {
    const actual = effectiveRecipe(base, 5)
    expect(actual.status).toBe('prepared')
    expect(actual.meal.ingredients.map(confirmedIngredientAmount)).toEqual(['2 cans', '¼'])
    expect(actual.meal.steps[0]).toEqual({ text: 'Heat 1½ cans; reserve ½. Add ¼ lemon.', ingredients: [0, 1], timer_minutes: 7 })
    const preview = effectiveRecipe(base, 6)
    expect(preview.status).toBe('preview')
    expect(preview.meal.ingredients[0].quantity).toBe(2.25)
    expect(preview.meal.steps).toEqual(base.steps)
    expect(effectiveRecipe(base, 5)).toEqual(actual)
  })
  it('preserves identity quarter counts and labels unbuilt, legacy, and original recipes', () => {
    const identity: Meal = { ...base, prepared: base.prepared ? { ...base.prepared, servings: 2, ingredients: base.ingredients, steps: base.steps } : null }
    expect(effectiveRecipe(identity, 2).meal.ingredients.map(confirmedIngredientAmount)).toEqual(['¾ can', '¼'])
    expect(effectiveRecipe({ ...base, prepared: null }, 5).status).toBe('preview')
    expect(effectiveRecipe(base, 2, true).status).toBe('original')
    const legacy: Meal = { ...base, prepared: base.prepared ? { ...base.prepared, legacy_scaled: true } : null }
    expect(effectiveRecipe(legacy, 5).status).toBe('legacy')
  })
  it.each([.0625, 1.88, 3.01, .004])('does not cosmetically round confirmed %s', (quantity) => {
    expect(confirmedIngredientAmount({ name: 'amount', quantity, unit: null, preparation: null })).toBe(String(quantity))
  })
})

it('keeps the complete original recipe and base yield while picking for another order count', () => {
  const original: Meal = { id: 'base', name: 'Base recipe', servings: 2, revision: 0,
    description: null, ingredient_count: 1, active_time_minutes: 5, total_time_minutes: 10,
    rating: null, picture: null, ingredients: [{ name: 'rice', quantity: .33, unit: 'cup', preparation: null }],
    steps: [{ text: 'Cook rice.', ingredients: [0], timer_minutes: 5 }] }
  const selected = effectiveRecipe(original, 4, true)
  expect(selected.meal).toBe(original)
  expect(selected.status).toBe('original')
})
