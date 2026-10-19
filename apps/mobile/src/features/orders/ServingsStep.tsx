import type { GroceryOrder } from '../../lib/api/types'
import { SERVINGS } from '../../lib/api/limits'
import { plural } from '../../lib/format'
import { Button } from '../../ui/Button'
import { Group } from '../../ui/Group'
import { Notice } from '../../ui/Notice'
import { Stepper } from '../../ui/Stepper'
import { MealRow } from '../meals/MealRow'
import { recipeCacheContext } from '../meals/servings'
import { pickedMeals } from './batches'
import { FlowFrame, FooterSummary } from './FlowFrame'
import { useOrderSession } from './lock'
import { useEditsPending, useSetServings } from './orders'
import styles from './Steps.module.css'
import { stepIndex } from './steps'
import { recipeBlockers } from './recipeChangesState'

interface ServingsStepProps {
  order: GroceryOrder
  startError?: string | null
  onOpenMeal: (mealId: string, servings?: number) => void
  onBack: () => void
  onNext: () => void
  onClose: () => void
}

/** How much of each picked meal to make: they start at the household's default. */
export function ServingsStep({ order, onOpenMeal, onBack, onNext, onClose, startError }: ServingsStepProps) {
  const session = useOrderSession()
  const setServings = useSetServings()
  // Moving on waits until every change is saved.
  const saving = useEditsPending()
  const changes = new Map(recipeBlockers(order).map((change) => [change.meal_id, change]))
  const meals = pickedMeals(order)
  const servings = meals.reduce((total, pick) => total + pick.servings, 0)

  return (
    <FlowFrame
      step={stepIndex('servings')}
      bodyClassName={styles.servingsBody}
      onBack={onBack}
      onClose={onClose}
      notice={(startError || setServings.isError) && <Notice>Couldn't save that: {startError ?? setServings.error?.message}</Notice>}
      title="Servings"
      lede={`How much of each meal to make for Order ${order.number}.`}
      footer={
        <div className={styles.servingsFooter}>
          <FooterSummary
            title={`${plural(meals.length, 'meal')} · ${plural(servings, 'serving')}`}
            detail={changes.size === 0 ? 'The shopping list is made for these' : undefined}
          />
          <div className={styles.footerAction}>
            {changes.size > 0 && <span className={styles.reviewCount}>{changes.size === 1 ? '1 meal needs review' : `${changes.size} meals need review`}</span>}
            <Button large onClick={onNext} disabled={saving || changes.size > 0 || meals.length === 0}>
              Next
            </Button>
          </div>
        </div>
      }
    >
      <div className={styles.groups}>
        <Group footerIcon="users" footer="Servings start at the household's default, in Settings.">
          {meals.map(({ meal, servings: count }) => {
            const change = changes.get(meal.id)
            return (
              <MealRow
                key={meal.id}
                meal={meal}
                prefetchContext={recipeCacheContext(order, meal.id, false)}
                onOpen={() => onOpenMeal(meal.id, count)}
                fullName
                recipeChange={change?.state}
                control={
                  <Stepper
                    inRow
                    value={count}
                    min={SERVINGS.min}
                    max={SERVINGS.max}
                    label="Servings"
                    onChange={(value) =>
                      setServings.mutate({ kind: 'servings', orderId: order.id, mealId: meal.id, servings: value, session })
                    }
                  />
                }
              />
            )
          })}
        </Group>
      </div>
    </FlowFrame>
  )
}
