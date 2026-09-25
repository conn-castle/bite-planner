import { IonIcon, useIonRouter } from '@ionic/react'
import { useQuery } from '@tanstack/react-query'
import { chevronForward } from 'ionicons/icons'
import { useParams } from 'react-router-dom'
import { mealFacts } from '../meals/facts'
import { Notice } from '../../ui/Notice'
import { Page } from '../../ui/Page'
import { Skeleton } from '../../ui/Skeleton'
import { mealCount } from './format'
import { jobMealsQuery } from '../meals/meals'
import { useJobs } from './requests'
import styles from './RequestPage.module.css'

/** The meals one request produced. */
export function RequestPage() {
  const { jobId = '' } = useParams()
  const router = useIonRouter()
  const job = useJobs().data?.find((candidate) => candidate.id === jobId)
  const meals = useQuery(jobMealsQuery(jobId))
  const count = meals.data?.length ?? job?.meals.length

  return (
    <Page title={count != null ? mealCount(count) : 'Meals'} backHref="/history">
      {job && (
        <p className={styles.eyebrow}>
          {new Date(job.created_at).toLocaleString(undefined, {
            weekday: 'long',
            month: 'long',
            day: 'numeric',
            hour: 'numeric',
            minute: '2-digit',
          })}
        </p>
      )}
      <h1 className={styles.title}>{count != null ? `${mealCount(count)} to try` : 'Meals'}</h1>

      {meals.isError && (
        <Notice action={<button onClick={() => void meals.refetch()}>Try again</button>}>
          Couldn't load these meals: {meals.error.message}
        </Notice>
      )}
      {meals.isPending && !meals.isError && (
        <div className={styles.list}>
          <Skeleton height={124} />
          <Skeleton height={124} />
          <Skeleton height={124} />
        </div>
      )}
      {meals.data?.length === 0 && <p className={styles.none}>This request has no meals.</p>}
      <div className={styles.list}>
        {meals.data?.map((meal, index) => (
          <button
            key={meal.id}
            type="button"
            className={styles.meal}
            style={{ animationDelay: `${index * 40}ms` }}
            onClick={() => router.push(`/history/${jobId}/meals/${meal.id}`)}
          >
            <span className={styles.name}>{meal.name}</span>
            {meal.description && <span className={styles.description}>{meal.description}</span>}
            <span className={styles.facts}>{mealFacts(meal).join(' · ')}</span>
            <IonIcon icon={chevronForward} className={styles.chevron} aria-hidden="true" />
          </button>
        ))}
      </div>
    </Page>
  )
}
