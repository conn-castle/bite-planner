import type { ReactNode } from 'react'
import type { MealSummary, RecipeChange } from '../../lib/api/types'
import { plural } from '../../lib/format'
import { MealArt } from '../../ui/art'
import { RecipeChangeStatus } from '../../ui/RecipeChangeStatus'
import styles from './MealRow.module.css'
import { ListRow } from '../../ui/Group'
import { usePrefetchMeal } from './cached'
import { formatMinutes, mealMinutes } from './facts'
import { MealRatingMark } from './MealRating'

interface MealRowProps {
  meal: MealSummary
  onOpen: () => void
  prefetchContext?: string
  /** Shows the meal's rating: for meals of placed orders. */
  rated?: boolean
  /** How many were ordered of it, shown with its time: for meals of placed orders. */
  servings?: number
  /** A control at the end, e.g. its servings; the rest of the row still opens the meal. */
  control?: ReactNode
  /** Servings shows the entire name; other contexts retain their existing layout. */
  fullName?: boolean
  /** Used with fullName to show the overflowing picture badge and recipe-change status. */
  recipeChange?: RecipeChange['state']
}

/** A meal in a list of meals: its picture, time, and name, and the rating once it's been ordered. */
export function MealRow({ meal, onOpen, rated = false, servings, control, fullName = false, recipeChange, prefetchContext }: MealRowProps) {
  const prefetch = usePrefetchMeal(prefetchContext)
  const minutes = mealMinutes(meal)
  const facts: string | undefined = [minutes != null && formatMinutes(minutes), servings != null && plural(servings, 'serving')]
    .filter((part) => part !== false).join(' · ') || undefined
  return (
    <ListRow
      art={<><MealArt meal={meal} />{recipeChange && <RecipeChangeStatus state={recipeChange} presentation="badge" />}</>}
      overline={recipeChange ? <span className={styles.metadata}>{facts && <span>{facts}</span>}<RecipeChangeStatus state={recipeChange} presentation="caption" /></span> : facts}
      label={meal.name}
      mark={rated ? <MealRatingMark meal={meal} /> : undefined}
      onClick={onOpen}
      onPressIntent={() => prefetch(meal.id)}
      control={control}
      wrapLabel={fullName}
    />
  )
}
