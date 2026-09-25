import { IonModal } from '@ionic/react'
import { useState } from 'react'
import { Button } from '../../ui/Button'
import { Stepper } from '../../ui/Stepper'
import { MAX_MEALS, MIN_MEALS } from './requests'
import styles from './RequestSheet.module.css'

const DEFAULT_COUNT = 5

interface RequestSheetProps {
  isOpen: boolean
  onDismiss: () => void
  onRequest: (count: number) => void
}

/** A bottom sheet for asking the agent for new meals. */
export function RequestSheet({ isOpen, onDismiss, onRequest }: RequestSheetProps) {
  const [count, setCount] = useState(DEFAULT_COUNT)

  return (
    <IonModal
      isOpen={isOpen}
      onDidDismiss={onDismiss}
      className="sheet-auto-height"
      breakpoints={[0, 1]}
      initialBreakpoint={1}
    >
      <div className={styles.sheet}>
        <h2 className={styles.title}>New meals</h2>
        <p className={styles.text}>
          Castle Chef comes up with easy dinners for the family. It takes a minute or two.
        </p>
        <div className={styles.picker}>
          <Stepper
            value={count}
            min={MIN_MEALS}
            max={MAX_MEALS}
            onChange={setCount}
            label="Meals"
            size="large"
          />
          <span className={styles.unit}>{count === 1 ? 'meal' : 'meals'}</span>
        </div>
        <Button block onClick={() => onRequest(count)}>
          Cook up {count} {count === 1 ? 'meal' : 'meals'}
        </Button>
      </div>
    </IonModal>
  )
}
