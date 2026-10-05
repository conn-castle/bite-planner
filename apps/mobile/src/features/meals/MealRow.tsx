import type { ReactNode } from 'react'
import type { MealSummary } from '../../lib/api/types'
import { plural } from '../../lib/format'
import { MealArt } from '../../ui/art'
import { ListRow } from '../../ui/Group'
import { usePrefetchMeal } from './cached'
import { formatMinutes, mealMinutes } from './facts'
import { MealRatingMark } from './MealRating'

interface MealRowProps {
  meal: MealSummary
  onOpen: () => void
  /** Shows the meal's rating: for meals of placed orders. */
  rated?: boolean
  /** How many were ordered of it, shown with its time: for meals of placed orders. */
  servings?: number
  /** A control at the end, e.g. its servings; the rest of the row still opens the meal. */
  control?: ReactNode
}

/** A meal in a list of meals: its picture, time, and name, and the rating once it's been ordered. */
export function MealRow({ meal, onOpen, rated = false, servings, control }: MealRowProps) {
  const prefetch = usePrefetchMeal()
  const minutes = mealMinutes(meal)
  return (
    <ListRow
      art={<MealArt />}
      overline={[minutes != null && formatMinutes(minutes), servings != null && plural(servings, 'serving')]
        .filter((part) => part !== false)
        .join(' · ') || undefined}
      label={meal.name}
      mark={rated ? <MealRatingMark meal={meal} /> : undefined}
      onClick={onOpen}
      onPressIntent={() => prefetch(meal.id)}
      control={control}
    />
  )
}
