import type { Meal, MealIngredient, MealPicture, ProposedRecipe } from '../../lib/api/types'
import { ingredientAmount } from './servings'
import type { DiffLine, DiffSection } from '../../ui/RecipeDiff'


/** LCS keeps unchanged context when the agent inserts/removes or reorders lines. */
export function diffLines(before: readonly string[], after: readonly string[]): DiffLine[] {
  const lengths: number[][] = Array.from({ length: before.length + 1 }, () => Array<number>(after.length + 1).fill(0))
  for (let i = before.length - 1; i >= 0; i--) {
    for (let j = after.length - 1; j >= 0; j--) {
      lengths[i][j] = before[i] === after[j] ? lengths[i + 1][j + 1] + 1 : Math.max(lengths[i + 1][j], lengths[i][j + 1])
    }
  }
  const lines: DiffLine[] = []
  let i = 0, j = 0
  while (i < before.length || j < after.length) {
    if (i < before.length && j < after.length && before[i] === after[j]) {
      lines.push({ kind: 'same', text: before[i++] }); j++
    } else if (i < before.length && (j === after.length || lengths[i + 1][j] >= lengths[i][j + 1])) {
      lines.push({ kind: 'removed', text: before[i++] })
    } else {
      lines.push({ kind: 'added', text: after[j++] })
    }
  }
  return lines
}

function ingredientLine(ingredient: MealIngredient): string {
  return [ingredientAmount(ingredient, 2, 2), ingredient.name].filter(Boolean).join(' ') + (ingredient.preparation ? `, ${ingredient.preparation}` : '')
}
const minutes = (value: number | null, label: string): string => `${label}: ${value === null ? 'not listed' : `${value} min`}`
function stepLine(text: string, timer: number | null, ingredients: string[]): string {
  return `${text}\nTimer: ${timer === null ? 'none' : `${timer} min`}\nUses: ${[...new Set(ingredients)].sort().join(', ') || 'none'}`
}

/** Every cooking metadata change is visible, including previously missing timers. */
export function recipeDiff(current: Meal, proposal: ProposedRecipe): DiffSection[] {
  return [
    { title: 'Recipe facts', lines: diffLines(
      [current.name, current.description ?? 'No description', minutes(current.total_time_minutes, 'Total'), minutes(current.active_time_minutes, 'Hands-on'), `${current.ingredient_count} ingredients`, `Recipe for ${current.servings} servings`],
      [proposal.name, proposal.description ?? 'No description', minutes(proposal.total_time_minutes, 'Total'), minutes(proposal.active_time_minutes, 'Hands-on'), `${proposal.ingredient_count} ingredients`, 'Recipe for 2 servings'],
    ) },
    { title: 'Ingredients', lines: diffLines(current.ingredients.map(ingredientLine), proposal.ingredients.map(ingredientLine)) },
    { title: 'Steps', lines: diffLines(
      current.steps.map((s) => stepLine(s.text, s.timer_minutes, s.ingredients.map((p) => current.ingredients[p].name))),
      proposal.steps.map((s) => stepLine(s.text, s.timer_minutes, s.ingredients)),
    ) },
  ]
}

export function proposalPicture(proposal: ProposedRecipe): MealPicture {
  return { dish: proposal.dish, ingredients: proposal.ingredients.flatMap((i) => i.icon !== null && i.color !== null ? [{ icon: i.icon, color: i.color }] : []) }
}
