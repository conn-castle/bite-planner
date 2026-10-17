import type { GroceryOrder } from '../../lib/api/types'
import { RecipeChangeStatus } from '../../ui/RecipeChangeStatus'
import { suggestions } from './batches'
import styles from './RecipeChanges.module.css'
import { recipeBlockers, unselectedChanges } from './recipeChangesState'

/** The same grouped, reachable recipe statuses at Servings, List and Review boundaries. */
export function RecipeChanges({ order, onOpenMeal, unselected = false }: {
  order: GroceryOrder; onOpenMeal: (mealId: string, servings?: number) => void; unselected?: boolean
}) {
  const changes = unselected ? unselectedChanges(order) : recipeBlockers(order)
  const names = new Map(suggestions(order).map((m) => [m.id, m.name]))
  if (!changes.length) return null
  const title: string = unselected ? 'Changes to meals you haven’t added' : 'Recipe changes need attention'
  return <section className={styles.group} aria-label={title}>
    <h3>{title}</h3>
    <p>{unselected
      ? 'Starting shopping means these changes to meals you haven’t added can no longer be applied. Review them now, or continue with the current recipes.'
      : 'Decide on these recipe changes before shopping or placing your order. Only selected meals hold up Next.'}</p>
    <ul>
      {changes.map((change) => <li key={change.job_id}><RecipeChangeStatus state={change.state} mealName={names.get(change.meal_id) ?? 'Recipe'}
        onOpen={() => onOpenMeal(change.meal_id, order.picks.find((pick) => pick.meal_id === change.meal_id)?.servings)} /></li>)}
    </ul>
  </section>
}
