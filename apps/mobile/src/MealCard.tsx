import { useState } from 'react'
import { formatQuantity, formatUnit, scaleIngredient } from './servings'
import type { Meal } from './types'

// Servings picked per meal, kept while the app is open so switching views doesn't reset them.
const pickedServings = new Map<string, number>()

export function MealCard({ meal }: { meal: Meal }) {
  const [servings, setServingsState] = useState(() => pickedServings.get(meal.id) ?? meal.servings)
  const setServings = (value: number) => {
    pickedServings.set(meal.id, value)
    setServingsState(value)
  }
  const ingredients = [...meal.meal_ingredient].sort((a, b) => a.position - b.position)

  return (
    <article className="card">
      <h3>{meal.name}</h3>
      {meal.description && <p className="muted">{meal.description}</p>}
      <p className="muted">
        {meal.active_time_minutes != null && `${meal.active_time_minutes} min active`}
        {meal.active_time_minutes != null && meal.total_time_minutes != null && ' · '}
        {meal.total_time_minutes != null && `${meal.total_time_minutes} min total`}
      </p>

      <div className="servings">
        <span>Servings</span>
        <button
          onClick={() => setServings(servings - 1)}
          disabled={servings <= 1}
          aria-label="Fewer servings"
        >
          −
        </button>
        <strong>{servings}</strong>
        <button onClick={() => setServings(servings + 1)} aria-label="More servings">
          +
        </button>
      </div>

      <h4>Ingredients</h4>
      <ul>
        {ingredients.map((ingredient) => {
          const quantity =
            ingredient.quantity != null
              ? scaleIngredient(ingredient.quantity, ingredient.unit, meal.servings, servings)
              : null
          return (
            <li key={ingredient.id}>
              {quantity != null && `${formatQuantity(quantity)} `}
              {ingredient.unit && `${formatUnit(ingredient.unit, quantity ?? 1)} `}
              {ingredient.name}
              {ingredient.preparation && `, ${ingredient.preparation}`}
            </li>
          )
        })}
      </ul>

      <h4>Steps</h4>
      <ol>
        {meal.steps.map((step, index) => (
          <li key={index}>{step}</li>
        ))}
      </ol>
    </article>
  )
}
