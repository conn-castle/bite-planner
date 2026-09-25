import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import type { Meal } from '../../lib/api/types'
import { Notice } from '../../ui/Notice'
import { NavPage } from '../../ui/Page'
import { Skeleton } from '../../ui/Skeleton'
import { Stepper } from '../../ui/Stepper'
import { formatMinutes } from './facts'
import styles from './MealPage.module.css'
import { mealQuery } from './meals'
import { formatQuantity, formatUnit, scaleIngredient } from './servings'

const MAX_SERVINGS = 24

// Servings picked per meal, kept while the app is open so going back and forth doesn't reset them.
const pickedServings = new Map<string, number>()

/** A full recipe, pushed onto a nav: facts, servings, ingredients scaled to them, and the method. */
export function MealPage({ mealId }: { mealId: string }) {
  const meal = useQuery(mealQuery(mealId))

  return (
    <NavPage title={meal.data?.name ?? 'Recipe'}>
      {meal.isError && (
        <Notice action={<button onClick={() => void meal.refetch()}>Try again</button>}>
          Couldn't load this recipe: {meal.error.message}
        </Notice>
      )}
      {meal.isPending && !meal.isError && (
        <div className={styles.loading}>
          <Skeleton height={44} width="80%" radius={10} />
          <Skeleton height={20} width="95%" radius={8} />
          <Skeleton height={84} />
          <Skeleton height={220} />
        </div>
      )}
      {meal.data && <Recipe meal={meal.data} />}
    </NavPage>
  )
}

function Recipe({ meal }: { meal: Meal }) {
  const [servings, setServingsState] = useState(() => pickedServings.get(meal.id) ?? meal.servings)
  const setServings = (value: number) => {
    pickedServings.set(meal.id, value)
    setServingsState(value)
  }
  const ingredients = meal.ingredients
  const facts = [
    meal.active_time_minutes != null && { label: 'Active', value: formatMinutes(meal.active_time_minutes) },
    meal.total_time_minutes != null && { label: 'Total', value: formatMinutes(meal.total_time_minutes) },
    { label: 'Ingredients', value: String(ingredients.length) },
  ].filter((fact) => fact !== false)

  return (
    <article className={styles.recipe}>
      <h1 className={styles.title}>{meal.name}</h1>
      {meal.description && <p className={styles.description}>{meal.description}</p>}

      <dl className={styles.facts}>
        {facts.map((fact) => (
          <div key={fact.label} className={styles.fact}>
            <dt>{fact.label}</dt>
            <dd>{fact.value}</dd>
          </div>
        ))}
      </dl>

      <div className={styles.servings}>
        <span className={styles.servingsLabel}>
          Servings
          {servings !== meal.servings && (
            <button type="button" className={styles.reset} onClick={() => setServings(meal.servings)}>
              Reset to {meal.servings}
            </button>
          )}
        </span>
        <Stepper value={servings} min={1} max={MAX_SERVINGS} onChange={setServings} label="Servings" />
      </div>

      <h2 className={styles.heading}>Ingredients</h2>
      <ul className={styles.ingredients}>
        {ingredients.map((ingredient, index) => {
          const quantity =
            ingredient.quantity != null
              ? scaleIngredient(ingredient.quantity, ingredient.unit, meal.servings, servings)
              : null
          return (
            // Ingredients have no id; their order is fixed.
            <li key={index} className={styles.ingredient}>
              <span className={styles.amount}>
                {quantity != null && formatQuantity(quantity)}
                {ingredient.unit && ` ${formatUnit(ingredient.unit, quantity ?? 1)}`}
              </span>
              <span className={styles.item}>
                {ingredient.name}
                {ingredient.preparation && <span className={styles.prep}>, {ingredient.preparation}</span>}
              </span>
            </li>
          )
        })}
      </ul>

      <h2 className={styles.heading}>Method</h2>
      <ol className={styles.steps}>
        {meal.steps.map((step, index) => (
          <li key={index} className={styles.step}>
            <span className={styles.number} aria-hidden="true">
              {index + 1}
            </span>
            <p>{step}</p>
          </li>
        ))}
      </ol>
    </article>
  )
}
