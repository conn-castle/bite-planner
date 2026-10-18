import type { GroceryOrder, Meal, MealIngredient } from '../../lib/api/types'

/** Scales a recipe quantity from the meal's servings to the servings the user picked. */
export function scaleQuantity(quantity: number, fromServings: number, toServings: number): number {
  return (quantity * toServings) / fromServings
}

/**
 * Scales an ingredient. Ingredients without a unit are counted whole (eggs,
 * garlic cloves), so their scaled count rounds to the nearest half, at least ½.
 */
export function scaleIngredient(
  quantity: number,
  unit: string | null,
  fromServings: number,
  toServings: number,
): number {
  const scaled = scaleQuantity(quantity, fromServings, toServings)
  return unit ? scaled : Math.max(0.5, Math.round(scaled * 2) / 2)
}

const FRACTIONS: [number, string][] = [
  [1 / 8, '⅛'],
  [1 / 4, '¼'],
  [1 / 3, '⅓'],
  [3 / 8, '⅜'],
  [1 / 2, '½'],
  [5 / 8, '⅝'],
  [2 / 3, '⅔'],
  [3 / 4, '¾'],
  [7 / 8, '⅞'],
]
// How far a value may be from a kitchen fraction and still be shown as one.
const TOLERANCE = 0.04

/** Formats a quantity the way recipes do, e.g. 0.375 -> "⅜", 2.25 -> "2¼", 21 -> "21". */
export function formatQuantity(quantity: number, tolerance: number = TOLERANCE): string {
  if (tolerance === TOLERANCE && quantity < 1 / 8 - tolerance) return '<⅛'
  const whole = Math.floor(quantity)
  const remainder = quantity - whole
  if (remainder <= tolerance && whole > 0) return String(whole)
  if (remainder >= 1 - tolerance) return String(whole + 1)
  const [value, glyph] = FRACTIONS.reduce((best, candidate) =>
    Math.abs(candidate[0] - remainder) < Math.abs(best[0] - remainder) ? candidate : best,
  )
  if (Math.abs(value - remainder) <= tolerance) return whole > 0 ? `${whole}${glyph}` : glyph
  return tolerance === TOLERANCE ? String(Math.round(quantity * 100) / 100) : String(quantity)
}

// Units that change form with the quantity. Abbreviations (lb, oz, tbsp) do not.
const UNIT_FORMS: [string, string][] = [
  ['bunch', 'bunches'],
  ['can', 'cans'],
  ['clove', 'cloves'],
  ['cup', 'cups'],
  ['head', 'heads'],
  ['jar', 'jars'],
  ['link', 'links'],
  ['package', 'packages'],
  ['piece', 'pieces'],
  ['pinch', 'pinches'],
  ['slice', 'slices'],
  ['stick', 'sticks'],
]

/** Uses the singular or plural form of a unit to match the quantity, e.g. 1 cup, 2 cups. */
export function formatUnit(unit: string, quantity: number): string {
  const forms = UNIT_FORMS.find(([one, many]) => [one, many].includes(unit.toLowerCase()))
  if (!forms) return unit
  return quantity > 1 ? forms[1] : forms[0]
}

/**
 * An ingredient's amount for `servings` of a recipe that makes `recipeServings`,
 * e.g. "1½ lb" or "3"; empty when it has no quantity or unit ("salt, to taste").
 */
export function ingredientAmount(ingredient: MealIngredient, recipeServings: number, servings: number): string {
  const quantity =
    ingredient.quantity != null ? scaleIngredient(ingredient.quantity, ingredient.unit, recipeServings, servings) : null
  return [quantity != null && formatQuantity(quantity), ingredient.unit && formatUnit(ingredient.unit, quantity ?? 1)]
    .filter(Boolean)
    .join(' ')
}

/** Format saved cooking consumption without changing counts or approximating fractions. */
export function confirmedIngredientAmount(ingredient: MealIngredient): string {
  const quantity = ingredient.quantity
  return [quantity != null && formatQuantity(quantity, 1e-9),
    ingredient.unit && formatUnit(ingredient.unit, quantity ?? 1)].filter(Boolean).join(' ')
}

/** Format an already-selected quantity; previews must not be scaled a second time. */
export function recipeIngredientAmount(ingredient: MealIngredient, status: EffectiveRecipe['status']): string {
  if (status === 'prepared' || status === 'legacy') return confirmedIngredientAmount(ingredient)
  const quantity = ingredient.quantity
  return [quantity != null && formatQuantity(quantity),
    ingredient.unit && formatUnit(ingredient.unit, quantity ?? 1)].filter(Boolean).join(' ')
}

export interface EffectiveRecipe {
  meal: Meal
  status: 'prepared' | 'legacy' | 'preview' | 'original'
}

/** Select the saved recipe only at its order yield; every what-if starts at the base. */
export function effectiveRecipe(base: Meal, servings: number, picking: boolean = false): EffectiveRecipe {
  if (picking) return { meal: base, status: 'original' }
  const prepared = base.prepared
  if (prepared && prepared.base_revision === base.revision && prepared.servings === servings) {
    return { meal: { ...base, servings, ingredients: prepared.ingredients, steps: prepared.steps },
      status: prepared.legacy_scaled ? 'legacy' : 'prepared' }
  }
  return { meal: { ...base, servings, ingredients: base.ingredients.map((i) => ({ ...i,
    quantity: i.quantity === null ? null : scaleIngredient(i.quantity, i.unit, base.servings, servings),
  })) }, status: 'preview' }
}

/** Scope persisted recipe data to the current source and build transition, not only revision. */
export function recipeCacheContext(order: GroceryOrder | undefined, mealId: string, placed: boolean, buildId?: string): string | undefined {
  if (!order) return placed ? `placed:${buildId ?? 'unknown'}` : undefined
  const list = order.shopping_list
  return JSON.stringify([order.id, order.status, order.picks,
    order.jobs.flatMap((j) => j.meals).find((m) => m.id === mealId)?.revision,
    list?.job.id, list?.job.status, list?.current])
}
