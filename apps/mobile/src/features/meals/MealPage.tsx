import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Button, IconButton } from '../../ui/Button'
import type { Meal, MealSummary } from '../../lib/api/types'
import { Icon } from '../../ui/Icon'
import { Notice } from '../../ui/Notice'
import { NavPage } from '../../ui/Page'
import { Skeleton } from '../../ui/Skeleton'
import { Stepper } from '../../ui/Stepper'
import { cachedMealSummary } from './cached'
import { CookView } from './CookView'
import styles from './MealPage.module.css'
import { mealQuery } from './meals'
import { MealRatingButton } from './MealRating'
import { useRatingError } from './ratings'
import { ingredientAmount } from './servings'

const MAX_SERVINGS = 24

interface MealPageProps {
  mealId: string
  tab?: { href: string }
  /** Shows the rating in the toolbar: for meals of placed orders, not suggestions being picked. */
  rateable?: boolean
  /**
   * Where the quantities start: an order's servings for the meal, or null for the
   * recipe's own (a meal of an order whose servings aren't known).
   */
  servings: number | null
  /**
   * A suggestion not picked yet: its servings are the recipe's, with no control
   * to change them (only a pick has servings to choose).
   */
  suggestion?: boolean
}

/**
 * A full recipe, pushed onto a nav: facts, servings, ingredients scaled to
 * them, and the steps. The meal's name and facts show at once from a list
 * already loaded (Home, History, the suggestions) while the rest loads.
 */
export function MealPage({ mealId, tab, rateable = false, servings, suggestion = false }: MealPageProps) {
  const meal = useQuery(mealQuery(mealId))
  const queryClient = useQueryClient()
  const summary = meal.data ?? cachedMealSummary(queryClient, mealId)
  const ratingError = useRatingError([mealId])
  const startAt = suggestion ? null : servings
  // The servings chosen here; until then, where they start.
  const [chosen, setChosen] = useState<number | null>(null)
  const [cooking, setCooking] = useState(false)
  const recipe = meal.data
  const shownServings = recipe && (chosen ?? startAt ?? recipe.servings)
  // Cooking is for a meal on its way or eaten (from Orders or History), not one being picked.
  const cookable = tab !== undefined && recipe !== undefined && recipe.steps.length > 0

  return (
    <NavPage
      title={summary?.name ?? 'Recipe'}
      tab={tab}
      actions={rateable && summary ? <MealRatingButton meal={summary} /> : undefined}
      footer={
        cookable && (
          <Button block onClick={() => setCooking(true)}>
            <Icon name="flame" />
            Start cooking
          </Button>
        )
      }
    >
      {rateable && ratingError && <Notice>Couldn't save the rating: {ratingError.message}</Notice>}
      {meal.isError && (
        <Notice action={<button onClick={() => void meal.refetch()}>Try again</button>}>
          Couldn't load this recipe: {meal.error.message}
        </Notice>
      )}
      {summary ? (
        <article className={styles.recipe}>
          <RecipeHeader meal={summary} ingredientCount={meal.data?.ingredients.length} />
          {recipe && shownServings !== undefined ? (
            <RecipeBody
              meal={recipe}
              servings={shownServings}
              chosenServings={startAt ?? recipe.servings}
              onServings={setChosen}
              fixed={suggestion}
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

/** The name, description, and facts: all but the ingredient count come with the meal's summary. */
function RecipeHeader({ meal, ingredientCount }: { meal: MealSummary; ingredientCount: number | undefined }) {
  const facts = [
    meal.total_time_minutes != null && { label: 'min total', value: meal.total_time_minutes },
    meal.active_time_minutes != null && { label: 'hands-on', value: meal.active_time_minutes },
    { label: 'ingredients', value: ingredientCount },
  ].filter((fact) => fact !== false)

  return (
    <>
      <h1 className={styles.title}>{meal.name}</h1>
      {meal.description && <p className={styles.description}>{meal.description}</p>}

      <dl className={styles.facts}>
        {facts.map((fact) => (
          <div key={fact.label} className={styles.fact}>
            <dt>{fact.label}</dt>
            {fact.value != null ? (
              <dd>{fact.value}</dd>
            ) : (
              <dd className={styles.pending}>
                <Skeleton height={19} width={20} radius={5} />
              </dd>
            )}
          </div>
        ))}
      </dl>
    </>
  )
}

interface RecipeBodyProps {
  meal: Meal
  servings: number
  /** The servings chosen for it (an order's, else the recipe's own), which Reset goes back to. */
  chosenServings: number
  onServings: (servings: number) => void
  /** The recipe's own servings, with no control to change them. */
  fixed: boolean
}

function RecipeBody({ meal, servings, chosenServings, onServings: setServings, fixed }: RecipeBodyProps) {
  const ingredients = meal.ingredients

  return (
    <>
      <div className={styles.servings}>
        <span className={styles.servingsLabel}>
          <Icon name="users" />
          <span>{fixed ? `Serves ${meal.servings}` : 'Servings'}</span>
        </span>
        {!fixed && (
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
        )}
      </div>

      <h2 className={styles.heading}>Ingredients</h2>
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
