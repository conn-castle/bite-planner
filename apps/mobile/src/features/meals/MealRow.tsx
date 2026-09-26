import type { MealSummary } from '../../lib/api/types'
import { MealArt } from '../../ui/art'
import { ListRow } from '../../ui/Group'
import { usePrefetchMeal } from './cached'
import { formatMinutes } from './facts'
import { MealRatingMark } from './MealRating'

interface MealRowProps {
  meal: MealSummary
  onOpen: () => void
  /** Shows the meal's rating: for meals of placed orders. */
  rated?: boolean
}

/** A meal in a list of meals: its picture, time, and name, and the rating once it's been ordered. */
export function MealRow({ meal, onOpen, rated = false }: MealRowProps) {
  const prefetch = usePrefetchMeal()
  const minutes = meal.total_time_minutes ?? meal.active_time_minutes
  return (
    <ListRow
      art={<MealArt />}
      overline={minutes != null ? formatMinutes(minutes) : undefined}
      label={meal.name}
      mark={rated ? <MealRatingMark meal={meal} /> : undefined}
      onClick={onOpen}
      onPressIntent={() => prefetch(meal.id)}
    />
  )
}
