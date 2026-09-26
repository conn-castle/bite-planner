import type { MealSummary } from '../../lib/api/types'
import { RatingButton, RatingChoices, RatingMark } from '../../ui/Rating'
import { useRateMeal, useRating } from './ratings'

/** The shared rating controls, connected to a meal: they show and save the household's rating. */

/** The toolbar's button on a meal's page. */
export function MealRatingButton({ meal }: { meal: MealSummary }) {
  const rate = useRateMeal()
  return (
    <RatingButton
      value={useRating(meal)}
      label={meal.name}
      onChange={(rating) => rate.mutate({ mealId: meal.id, rating })}
    />
  )
}

/** The three choices in a row, e.g. in the prompt to rate last week's meals. */
export function MealRatingChoices({ meal }: { meal: MealSummary }) {
  const rate = useRateMeal()
  return (
    <RatingChoices
      value={useRating(meal)}
      label={meal.name}
      onChange={(rating) => rate.mutate({ mealId: meal.id, rating })}
    />
  )
}

/** The meal's rating as a small mark in a list row. */
export function MealRatingMark({ meal }: { meal: MealSummary }) {
  return <RatingMark rating={useRating(meal)} />
}
