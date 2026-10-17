import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type ReactNode } from 'react'
import { Button, IconButton } from '../../ui/Button'
import type { Meal, MealSummary, GroceryOrder } from '../../lib/api/types'
import { plural } from '../../lib/format'
import { Icon } from '../../ui/Icon'
import { Note } from '../../ui/Note'
import { Notice } from '../../ui/Notice'
import { NavPage } from '../../ui/Page'
import { Skeleton } from '../../ui/Skeleton'
import { Stepper } from '../../ui/Stepper'
import { Stats, type Stat } from '../../ui/Stats'
import { cachedMealSummary } from './cached'
import { CookView } from './CookView'
import styles from './MealPage.module.css'
import { mealQuery } from './meals'
import { MealRatingButton } from './MealRating'
import { useRatingError } from './ratings'
import { ingredientAmount } from './servings'
import { RecipeFeedback } from './RecipeFeedback'

const MAX_SERVINGS = 24

interface MealPageProps {
  mealId: string
  order?: GroceryOrder
  selection?: ReactNode
  selectionProblem?: string | null
  tab?: { href: string }
  /** Shows the rating in the toolbar: for meals of placed orders, not suggestions being picked. */
  rateable?: boolean
  /**
   * Where the quantities start: an order's servings for the meal, or null for the
   * recipe's own (a meal being picked, or of an order whose servings aren't known).
   */
  servings: number | null
  /**
   * Opened while picking meals: the recipe as written, with no servings shown
   * (they're chosen in the Servings step).
   */
  picking?: boolean
}

/**
 * A full recipe, pushed onto a nav: facts, servings, ingredients scaled to
 * them, and the steps. The meal's name and facts show at once from a list
 * already loaded (Home, History, the suggestions) while the rest loads.
 */
export function MealPage({ mealId, tab, rateable = false, servings, picking = false, order, selection, selectionProblem }: MealPageProps) {
  const meal = useQuery(mealQuery(mealId))
  const queryClient = useQueryClient()
  const orderSummary = order?.jobs.flatMap((job) => job.meals).find((m) => m.id === mealId)
  const expectedRevision = orderSummary?.revision
  const outdated = expectedRevision !== undefined && meal.data !== undefined && meal.data.revision < expectedRevision
  useEffect(() => {
    if (!outdated) return
    const key = mealQuery(mealId).queryKey
    void queryClient.cancelQueries({ queryKey: key, exact: true }).then(() =>
      queryClient.invalidateQueries({ queryKey: key, exact: true }))
  }, [expectedRevision, outdated, mealId, queryClient])
  const summary = !outdated && meal.data ? meal.data : orderSummary ?? cachedMealSummary(queryClient, mealId)
  const ratingError = useRatingError([mealId])
  // The servings chosen here; until then, where they start.
  const [chosen, setChosen] = useState<number | null>(null)
  const [cooking, setCooking] = useState(false)
  const recipe = outdated ? undefined : meal.data
  const shownServings = recipe && (chosen ?? servings ?? recipe.servings)
  // Cooking is for a meal on its way or eaten (from Orders or History), not one being picked.
  const cookable = tab !== undefined && recipe !== undefined && recipe.steps.length > 0

  return (
    <NavPage
      title={summary?.name ?? 'Recipe'}
      tab={tab}
      actions={selection ?? (rateable && summary ? <MealRatingButton meal={summary} /> : undefined)}
      footer={
        cookable && (
          <Button block onClick={() => setCooking(true)}>
            <Icon name="flame" />
            Start cooking
          </Button>
        )
      }
    >
      {selectionProblem && <Notice>Couldn't save that change: {selectionProblem}</Notice>}
      {rateable && ratingError && <Notice>Couldn't save the rating: {ratingError.message}</Notice>}
      {meal.isError && (
        <Notice action={<button onClick={() => void meal.refetch()}>Try again</button>}>
          Couldn't load this recipe: {meal.error.message}
        </Notice>
      )}
      {summary ? (
        <article className={styles.recipe}>
          <RecipeHeader meal={summary} />
          {order && <RecipeFeedback order={order} meal={recipe} summary={summary} />}
          {order?.recipe_changes.some((c) => c.meal_id === mealId && c.state === 'ready') ? null : recipe && shownServings !== undefined ? (
            <RecipeBody
              meal={recipe}
              servings={shownServings}
              chosenServings={servings ?? recipe.servings}
              onServings={setChosen}
              picking={picking}
            />
          ) : (
            !meal.isError && (
              <div className={styles.loading}>
                <Skeleton height={60} />
                <Skeleton height={220} />
              </div>
            )
          )}
        </article>
      ) : (
        !meal.isError && (
          <div className={styles.loading}>
            <Skeleton height={44} width="80%" radius={10} />
            <Skeleton height={20} width="95%" radius={8} />
            <Skeleton height={84} />
            <Skeleton height={220} />
          </div>
        )
      )}
      {cookable && shownServings !== undefined && (
        <CookView meal={recipe} servings={shownServings} isOpen={cooking} onClose={() => setCooking(false)} />
      )}
    </NavPage>
  )
}

/** The name, description, and facts from the meal's summary. */
function RecipeHeader({ meal }: { meal: MealSummary }) {
  const facts: Stat[] = [
    meal.total_time_minutes != null && { label: 'min total', value: meal.total_time_minutes },
    meal.active_time_minutes != null && { label: 'hands-on', value: meal.active_time_minutes },
    { label: 'ingredients', value: meal.ingredient_count },
  ].filter((fact) => fact !== false)

  return (
    <>
      <h1 className={styles.title}>{meal.name}</h1>
      {meal.description && <p className={styles.description}>{meal.description}</p>}

      <Stats stats={facts} variant="recipe" />
    </>
  )
}

interface RecipeBodyProps {
  meal: Meal
  servings: number
  /** The servings chosen for it (an order's, else the recipe's own), which Reset goes back to. */
  chosenServings: number
  onServings: (servings: number) => void
  /** Opened while picking meals: no servings control, just a footnote saying the amounts get scaled in the Servings step. */
  picking: boolean
}

function RecipeBody({ meal, servings, chosenServings, onServings: setServings, picking }: RecipeBodyProps) {
  const ingredients = meal.ingredients

  return (
    <>
      {!picking && (
        <div className={styles.servings}>
          <span className={styles.servingsLabel}>
            <Icon name="users" />
            <span>Servings</span>
          </span>
          <span className={styles.servingsControls}>
            {/* Only once they differ from what was chosen: there's nothing to go back to before. */}
            {servings !== chosenServings && (
              <IconButton
                variant="white"
                aria-label={`Back to ${chosenServings} servings`}
                onClick={() => setServings(chosenServings)}
              >
                <Icon name="undo" />
              </IconButton>
            )}
            <Stepper value={servings} min={1} max={MAX_SERVINGS} onChange={setServings} label="Servings" />
          </span>
        </div>
      )}

      <h2 className={styles.heading}>Ingredients{picking && '*'}</h2>
      <ul className={styles.ingredients}>
        {ingredients.map((ingredient, index) => (
          // Ingredients have no id; their order is fixed.
          <li key={index} className={styles.ingredient}>
            <span className={styles.amount}>{ingredientAmount(ingredient, meal.servings, servings)}</span>
            <span>
              {ingredient.name}
              {ingredient.preparation && `, ${ingredient.preparation}`}
            </span>
          </li>
        ))}
      </ul>
      {picking && (
        <Note icon="users">
          * This recipe is for {plural(meal.servings, 'serving')}. It's scaled to the servings you choose in the
          next step.
        </Note>
      )}

      <h2 className={styles.heading}>Steps</h2>
      <ol className={styles.ingredients}>
        {meal.steps.map((step, index) => (
          <li key={index} className={styles.ingredient}>
            {/* Not aria-hidden: without list markers (list-style: none), VoiceOver announces no step numbers of its own. */}
            <span className={styles.number}>{index + 1}</span>
            <span>{step.text}</span>
          </li>
        ))}
      </ol>
    </>
  )
}
