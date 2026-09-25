import { IonIcon } from '@ionic/react'
import { alertCircle, chevronForward } from 'ionicons/icons'
import type { Job } from '../../lib/api/types'
import { elapsed, mealCount, timeOfDay } from './format'
import { OPTIMISTIC_PREFIX } from './requests'
import styles from './RequestCard.module.css'
import { useNow } from './useNow'

const PREVIEW_MEALS = 3

interface RequestCardProps {
  job: Job
  onOpen: () => void
  onRetry: () => void
}

/** One meal request in History: its meals once ready, live progress while cooking. */
export function RequestCard({ job, onOpen, onRetry }: RequestCardProps) {
  const created = new Date(job.created_at)
  if (job.status === 'succeeded') {
    const hidden = job.meals.length - PREVIEW_MEALS
    return (
      <button type="button" className={`${styles.card} ${styles.pressable}`} onClick={onOpen}>
        <span className={styles.meta}>
          {mealCount(job.meals.length)} · {timeOfDay(created)}
          <IonIcon icon={chevronForward} className={styles.chevron} aria-hidden="true" />
        </span>
        <ul className={styles.meals}>
          {job.meals.slice(0, PREVIEW_MEALS).map((meal) => (
            <li key={meal.id}>{meal.name}</li>
          ))}
        </ul>
        {hidden > 0 && <span className={styles.more}>+ {hidden} more</span>}
      </button>
    )
  }
  if (job.status === 'failed') {
    return (
      <div className={styles.card}>
        <span className={`${styles.meta} ${styles.failed}`}>
          <IonIcon icon={alertCircle} aria-hidden="true" />
          Didn't finish · {mealCount(job.params.count)} · {timeOfDay(created)}
        </span>
        {job.error && <p className={styles.error}>{job.error}</p>}
        <button type="button" className={styles.retry} onClick={onRetry}>
          Try again
        </button>
      </div>
    )
  }
  return <CookingCard job={job} created={created} />
}

function CookingCard({ job, created }: { job: Job; created: Date }) {
  const now = useNow(1000)
  const sending = job.id.startsWith(OPTIMISTIC_PREFIX)
  return (
    <div className={`${styles.card} ${styles.cooking}`} aria-busy="true">
      <span className={styles.meta}>
        <span className={styles.pulse} aria-hidden="true" />
        {sending ? 'Sending request' : job.status === 'pending' ? 'Getting started' : 'Cooking'}
        {!sending && <span className={styles.timer}>{elapsed(created, now)}</span>}
      </span>
      <p className={styles.headline}>Cooking up {mealCount(job.params.count)}…</p>
      <p className={styles.hint}>Usually ready in a minute or two.</p>
      <span className={styles.progress} aria-hidden="true" />
    </div>
  )
}
