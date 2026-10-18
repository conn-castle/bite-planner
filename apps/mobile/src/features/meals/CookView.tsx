import { KeepAwake } from '@capacitor-community/keep-awake'
import { IonContent, IonFooter, IonHeader, IonModal, IonToolbar } from '@ionic/react'
import { useEffect, useRef, useState } from 'react'
import type { Meal, MealIngredient } from '../../lib/api/types'
import { errorMessage } from '../../lib/errorMessage'
import { errorFields, logger } from '../../lib/log'
import { useNow } from '../../lib/useNow'
import { Button, IconButton } from '../../ui/Button'
import { Icon } from '../../ui/Icon'
import { Notice } from '../../ui/Notice'
import { Note } from '../../ui/Note'
import { StepDots } from '../../ui/StepDots'
import styles from './CookView.module.css'
import { recipeIngredientAmount } from './servings'
import { loadTimer, startTimer, stopTimer, type StepTimer, useStepTimer } from './timer'

// The step each meal's cooking is at, kept while the app is open until Done.
const stepAt = new Map<string, number>()

/** Keeps the screen on while cooking (the cook's hands are busy), or lets it sleep again. */
function keepScreenOn(on: boolean) {
  ;(on ? KeepAwake.keepAwake() : KeepAwake.allowSleep()).catch((error: unknown) =>
    logger.warn('cooking.keep_awake_failed', { on, ...errorFields(error) }),
  )
}

interface CookViewProps {
  meal: Meal
  status: 'prepared' | 'legacy' | 'preview' | 'original'
  isOpen: boolean
  onClose: () => void
}

/**
 * Cooking a meal, one step at a time (spec/mockup.html: .cook): the step, its
 * timer when it has the cook wait, and the ingredients it uses. Full screen
 * over the app, like the order flow.
 */
export function CookView({ meal, status, isOpen, onClose }: CookViewProps) {
  const [at, setAt] = useState(0)
  const [problem, setProblem] = useState<string | null>(null)
  // Counts the cook's moves: a timer read from iOS on opening only moves to its
  // step if the cook hasn't moved (or closed the view) since.
  const moves = useRef(0)
  const timer = useStepTimer()
  const step = meal.steps[Math.min(at, meal.steps.length - 1)]
  const last = at >= meal.steps.length - 1
  const minutes = step.timer_minutes
  // Leaving the page with the view open (say, signing out) lets the screen sleep too.
  useEffect(() => () => keepScreenOn(false), [])

  function opening() {
    // Back where it was left (it starts over after Done). After the app has
    // closed, at its running timer's step: iOS has the timer, which outlives the app.
    const left = stepAt.get(meal.id)
    go(left ?? 0)
    const opened = moves.current
    loadTimer()
      .then((running) => {
        if (left === undefined && moves.current === opened && running?.mealId === meal.id) go(running.step)
      })
      .catch((error: unknown) => logger.warn('cooking.timer_load_failed', errorFields(error)))
  }

  function go(to: number) {
    stepAt.set(meal.id, to)
    setAt(to)
    setProblem(null)
  }

  function move(to: number) {
    moves.current++
    go(to)
  }

  function done() {
    moves.current++
    stepAt.delete(meal.id)
    onClose()
  }

  async function toggleTimer(minutes: number) {
    setProblem(null)
    try {
      if (timer?.mealId === meal.id && timer.step === at) await stopTimer()
      else await startTimer(meal, at, minutes)
    } catch (error) {
      logger.warn('cooking.timer_failed', errorFields(error))
      setProblem(errorMessage(error))
    }
  }

  return (
    <IonModal
      className="cc-full-screen"
      isOpen={isOpen}
      onWillPresent={opening}
      onDidPresent={() => keepScreenOn(true)}
      onDidDismiss={() => {
        moves.current++
        keepScreenOn(false)
        onClose()
      }}
    >
      <IonHeader className={styles.header}>
        <IonToolbar>
          <div className={styles.bar}>
            <IconButton variant="fill" aria-label="Close" onClick={onClose}>
              <Icon name="x" />
            </IconButton>
            <span className={styles.title}>{meal.name}</span>
            <span className={styles.spacer} />
          </div>
          <div className={styles.progress}>
            <StepDots steps={meal.steps.map((s) => s.text)} at={at} look="cooking" />
          </div>
        </IonToolbar>
      </IonHeader>
      <IonContent>
        <div key={at} className={styles.body}>
          {status === 'preview' && <Note icon="users">Provisional preview. Steps retain the original amounts.</Note>}
          {status === 'legacy' && <Note icon="users">Earlier order: scaled ingredients; original step amounts.</Note>}
          {problem && <Notice>{problem}</Notice>}
          <div className={styles.number}>
            Step {at + 1} of {meal.steps.length}
          </div>
          <p className={styles.text}>{step.text}</p>
          {minutes !== null && (
            <TimerButton
              minutes={minutes}
              running={timer?.mealId === meal.id && timer.step === at ? timer : null}
              onClick={() => void toggleTimer(minutes)}
            />
          )}
          {step.ingredients.length > 0 && (
            <div className={styles.uses}>
              <h3>You'll need</h3>
              <ul>
                {step.ingredients.map((position) => {
                  // The API checks every position is one of the meal's ingredients; one that isn't is left out.
                  const ingredient: MealIngredient | undefined = meal.ingredients[position]
                  if (ingredient === undefined) return null
                  return (
                    <li key={position}>
                      {[recipeIngredientAmount(ingredient, status), ingredient.name]
                        .filter(Boolean)
                        .join(' ')}
                    </li>
                  )
                })}
              </ul>
            </div>
          )}
        </div>
      </IonContent>
      <IonFooter className={styles.footer}>
        <div className={styles.footerBar}>
          <Button variant="secondary" large className={styles.back} disabled={at === 0} onClick={() => move(at - 1)}>
            Back
          </Button>
          {last ? (
            <Button variant="accent" large className={styles.next} onClick={done}>
              <Icon name="check" />
              Done
            </Button>
          ) : (
            <Button large className={styles.next} onClick={() => move(at + 1)}>
              Next step
            </Button>
          )}
        </div>
      </IonFooter>
    </IonModal>
  )
}

/** Starts the step's timer, or shows the time left on it (tapping it then stops it). */
function TimerButton({ minutes, running, onClick }: { minutes: number; running: StepTimer | null; onClick: () => void }) {
  return (
    <button type="button" className={`${styles.timer} ${running ? styles.running : ''} cc-hit`} onClick={onClick}>
      <Icon name="timer" />
      {running ? <TimeLeft endsAt={running.endsAt} /> : <span>Start {minutes} min timer</span>}
    </button>
  )
}

function TimeLeft({ endsAt }: { endsAt: number }) {
  const now = useNow(1000).getTime()
  const seconds = Math.max(0, Math.round((endsAt - now) / 1000))
  return (
    <span aria-label={`${Math.ceil(seconds / 60)} min left; tap to stop`}>
      {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, '0')} left
    </span>
  )
}
