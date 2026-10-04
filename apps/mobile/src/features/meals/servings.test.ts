import { describe, expect, it } from 'vitest'
import { formatQuantity, formatUnit, ingredientAmount, scaleIngredient, scaleQuantity } from './servings'

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
