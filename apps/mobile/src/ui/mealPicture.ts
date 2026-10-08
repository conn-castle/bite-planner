import type { MealColor, MealIcon, MealPicture, PicturedIngredient } from '../lib/api/types'

/** The palette the agent picks each pictured ingredient's color from. */
export const MEAL_COLORS: Record<MealColor, string> = {
  white: '#f7f3ea',
  cream: '#efe4c8',
  yellow: '#f2d24b',
  gold: '#e5bf62',
  tan: '#e3c08f',
  orange: '#ec8a3a',
  salmon: '#f08a6b',
  pink: '#e2b19a',
  red: '#e0503a',
  dark_red: '#b8352c',
  purple: '#9b4a6e',
  brown: '#8a4a3a',
  black: '#3d3442',
  light_green: '#a9c977',
  green: '#6f9a4a',
  dark_green: '#3f7a45',
}

export const INK = '#141311'

const MAINS: ReadonlySet<MealIcon> = new Set<MealIcon>([
  'fish',
  'shrimp',
  'drumstick',
  'steak',
  'pork_chop',
  'ground_meat',
  'tofu',
  'egg',
  'beans',
  'chickpeas',
])
// What the main ingredient sits on (a tortilla is a taco's shell instead).
const BEDS: ReadonlySet<MealIcon> = new Set<MealIcon>(['rice', 'pasta', 'sauce', 'tortilla'])

export interface PictureParts {
  /** Drawn largest, in front. */
  hero: PicturedIngredient
  /** Drawn under the hero (on a plate, in a bowl or skillet). */
  bed: PicturedIngredient
  /** Up to two, beside the hero. */
  sides: PicturedIngredient[]
  /** A taco's shell: its first tortilla, else its bed. */
  shell: PicturedIngredient
}

/**
 * The parts of a meal's picture, from its pictured ingredients in recipe order:
 * the first main ingredient (else the first side, else the first ingredient), the
 * first bed (rice, pasta, sauce, tortilla; else the next ingredient), and the next
 * two sides.
 */
export function pictureParts({ ingredients }: MealPicture): PictureParts {
  const [first] = ingredients
  if (!first) throw new Error('A meal picture has no pictured ingredients.')
  const hero =
    ingredients.find((item) => MAINS.has(item.icon)) ??
    ingredients.find((item) => !BEDS.has(item.icon)) ??
    first
  const bed =
    ingredients.find((item) => item !== hero && BEDS.has(item.icon)) ??
    ingredients.find((item) => item !== hero) ??
    hero
  const sides = ingredients.filter((item) => item !== hero && item !== bed && !BEDS.has(item.icon)).slice(0, 2)
  const shell = ingredients.find((item) => item.icon === 'tortilla') ?? bed
  return { hero, bed, sides, shell }
}

/** `hex` moved `amount` (0 to 1) of the way to `other`. */
export function mix(hex: string, other: string, amount: number): string {
  const channels = (color: string) => [1, 3, 5].map((index) => parseInt(color.slice(index, index + 2), 16))
  const to = channels(other)
  return `#${channels(hex)
    .map((value, index) => Math.round(value + ((to[index] ?? value) - value) * amount).toString(16).padStart(2, '0'))
    .join('')}`
}

/** The soft background behind a meal's picture: a tint of its first pictured ingredient. */
export function pictureTint(picture: MealPicture): string {
  const [first] = picture.ingredients
  if (!first) throw new Error('A meal picture has no pictured ingredients.')
  return mix(MEAL_COLORS[first.color], '#ffffff', 0.8)
}

/** A repeatable sequence of numbers in [0, 1) from `seed` (a meal's id): the same meal always draws the same. */
export function seededRandom(seed: string): () => number {
  let state = 2166136261
  for (const char of seed) state = Math.imul(state ^ (char.codePointAt(0) ?? 0), 16777619)
  return () => {
    state = (state + 0x6d2b79f5) | 0
    let t = Math.imul(state ^ (state >>> 15), 1 | state)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
