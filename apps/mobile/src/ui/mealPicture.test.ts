import { describe, expect, it } from 'vitest'
import type { MealIcon, MealPicture, PicturedIngredient } from '../lib/api/types'
import { pictureParts, pictureTint } from './mealPicture'

const item = (icon: MealIcon): PicturedIngredient => ({ icon, color: 'green' })
const plate = (...icons: MealIcon[]): MealPicture => ({ dish: 'plate', ingredients: icons.map(item) })

describe('pictureParts', () => {
  it('makes the first main ingredient the hero, even when listed after the rest', () => {
    const parts = pictureParts(plate('rice', 'stalks', 'steak', 'fish'))

    expect(parts.hero.icon).toBe('steak')
  })

  it('makes the first side the hero of a meal with no main ingredient', () => {
    const parts = pictureParts(plate('rice', 'mushroom', 'cheese_wedge'))

    expect([parts.hero.icon, parts.bed.icon, parts.sides.map((side) => side.icon)]).toEqual(['mushroom', 'rice', ['cheese_wedge']])
  })

  it('beds the hero on the first rice, pasta, sauce, or tortilla, else on the next ingredient', () => {
    expect(pictureParts(plate('drumstick', 'round', 'sauce', 'rice')).bed.icon).toBe('sauce')
    expect(pictureParts(plate('steak', 'round', 'stalks')).bed.icon).toBe('round')
  })

  it('puts the next two sides beside the hero, never a second bed', () => {
    const parts = pictureParts(plate('tofu', 'sauce', 'pepper', 'rice', 'stalks', 'round'))

    expect(parts.sides.map((side) => side.icon)).toEqual(['pepper', 'stalks'])
  })

  it("makes a taco's tortilla its shell", () => {
    const parts = pictureParts({ dish: 'tacos', ingredients: [item('beans'), item('tortilla'), item('round')] })

    expect([parts.hero.icon, parts.shell.icon, parts.sides.map((side) => side.icon)]).toEqual(['beans', 'tortilla', ['round']])
  })

  it('draws a meal with one pictured ingredient as that ingredient on itself', () => {
    const parts = pictureParts(plate('fish'))

    expect([parts.hero, parts.bed]).toEqual([item('fish'), item('fish')])
    expect(parts.sides).toEqual([])
  })
})

describe('pictureTint', () => {
  it("is a light tint of the first ingredient's color", () => {
    expect(pictureTint({ dish: 'bowl', ingredients: [{ icon: 'steak', color: 'dark_red' }, item('rice')] })).toBe('#f1d7d5')
  })
})
