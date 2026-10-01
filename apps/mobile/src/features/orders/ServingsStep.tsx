import type { GroceryOrder } from '../../lib/api/types'
import { SERVINGS } from '../../lib/api/limits'
import { Button } from '../../ui/Button'
import { Group } from '../../ui/Group'
import { Notice } from '../../ui/Notice'
import { Stepper } from '../../ui/Stepper'
import { MealRow } from '../meals/MealRow'
import { pickedMeals } from './batches'
import { FlowFrame, FooterSummary } from './FlowFrame'
import { plural } from './format'
import { useOrderSession } from './lock'
import { useEditsPending, useSetServings } from './orders'
import styles from './Steps.module.css'
import { stepIndex } from './steps'

interface ServingsStepProps {
  order: GroceryOrder
  onOpenMeal: (mealId: string, servings: number) => void
  onBack: () => void
  onNext: () => void
  onClose: () => void
}

/** How much of each picked meal to make: they start at the household's default. */
export function ServingsStep({ order, onOpenMeal, onBack, onNext, onClose }: ServingsStepProps) {
  const session = useOrderSession()
  const setServings = useSetServings()
  // Moving on waits until every change is saved.
  const saving = useEditsPending()
  const meals = pickedMeals(order)
  const servings = meals.reduce((total, pick) => total + pick.servings, 0)

  return (
    <FlowFrame
      step={stepIndex('servings')}
      onBack={onBack}
      onClose={onClose}
      notice={setServings.isError && <Notice>Couldn't save that: {setServings.error.message}</Notice>}
      title="Servings"
      lede={`How much of each meal to make for Order ${order.number}.`}
      footer={
        <>
          <FooterSummary
            title={`${plural(meals.length, 'meal')} · ${plural(servings, 'serving')}`}
            detail="The shopping list is made for these"
          />
          <Button large onClick={onNext} disabled={saving}>
            Next
          </Button>
        </>
      }
    >
      <div className={styles.groups}>
        <Group footerIcon="users" footer="Servings start at the household's default, in Settings.">
          {meals.map(({ meal, servings: count }) => (
            <MealRow
              key={meal.id}
              meal={meal}
              onOpen={() => onOpenMeal(meal.id, count)}
              control={
                <Stepper
                  inRow
                  value={count}
                  min={SERVINGS.min}
                  max={SERVINGS.max}
                  label="Servings"
                  onChange={(value) =>
                    setServings.mutate({ orderId: order.id, mealId: meal.id, servings: value, session })
                  }
                />
              }
            />
          ))}
        </Group>
      </div>
    </FlowFrame>
  )
}
