import { IonIcon, useIonRouter } from '@ionic/react'
import { useQueryClient } from '@tanstack/react-query'
import { add } from 'ionicons/icons'
import { useEffect, useState } from 'react'
import { haptics } from '../../lib/haptics'
import { PotArt } from '../../ui/art'
import { Button, IconButton } from '../../ui/Button'
import { EmptyState } from '../../ui/EmptyState'
import { Notice } from '../../ui/Notice'
import { Page } from '../../ui/Page'
import { Skeleton } from '../../ui/Skeleton'
import { groupByDay } from './days'
import styles from './HistoryPage.module.css'
import { RequestCard } from './RequestCard'
import { RequestSheet } from './RequestSheet'
import { jobMealsQuery } from '../meals/meals'
import { useFailedMealRequests, useJobs, useRequestMeals } from './requests'

// Recent finished requests whose meals are fetched ahead of a tap.
const PREFETCH_COUNT = 5

/** Every meal request in the household, newest first, grouped by day. */
export function HistoryPage() {
  const router = useIonRouter()
  const queryClient = useQueryClient()
  const jobs = useJobs()
  const request = useRequestMeals()
  const failed = useFailedMealRequests()
  const [sheetOpen, setSheetOpen] = useState(false)

  // Opening a finished request should be instant.
  useEffect(() => {
    const finished = (jobs.data ?? []).filter((job) => job.status === 'succeeded')
    for (const job of finished.slice(0, PREFETCH_COUNT)) {
      void queryClient.prefetchQuery(jobMealsQuery(job.id))
    }
  }, [jobs.data, queryClient])

  function requestMeals(count: number) {
    setSheetOpen(false)
    haptics.success()
    request.mutate({ jobId: crypto.randomUUID(), count })
  }

  const sections = groupByDay(jobs.data ?? [], (job) => new Date(job.created_at), new Date())
  const newRequest = (
    <IconButton variant="primary" aria-label="New meals" onClick={() => setSheetOpen(true)}>
      <IonIcon icon={add} aria-hidden="true" />
    </IconButton>
  )

  return (
    <Page title="History" largeTitle actions={newRequest} onRefresh={() => jobs.refetch()}>
      {failed.errors.length > 0 && (
        <div className={styles.notice}>
          <Notice action={<button onClick={failed.dismiss}>Dismiss</button>}>
            Couldn't request meals: {failed.errors[failed.errors.length - 1]?.message}
          </Notice>
        </div>
      )}
      {jobs.isError && (
        <div className={styles.notice}>
          <Notice action={<button onClick={() => void jobs.refetch()}>Try again</button>}>
            {jobs.data ? "Couldn't refresh" : "Couldn't load your meals"}: {jobs.error.message}
          </Notice>
        </div>
      )}

      {jobs.isPending && !jobs.isError && (
        <div className={styles.list}>
          <Skeleton height={20} width={90} radius={6} />
          <Skeleton height={148} />
          <Skeleton height={112} />
        </div>
      )}

      {jobs.data?.length === 0 && (
        <EmptyState
          art={<PotArt />}
          title="No meals yet"
          action={<Button onClick={() => setSheetOpen(true)}>Get meal ideas</Button>}
        >
          Ask Castle Chef for a few easy dinners to get started.
        </EmptyState>
      )}

      {sections.map((section) => (
        <section key={section.key} className={styles.section}>
          <h2 className={styles.day}>{section.label}</h2>
          <div className={styles.list}>
            {section.items.map((job) => (
              <RequestCard
                key={job.id}
                job={job}
                onOpen={() => router.push(`/history/${job.id}`)}
                onRetry={() => requestMeals(job.params.count)}
              />
            ))}
          </div>
        </section>
      ))}

      <RequestSheet
        isOpen={sheetOpen}
        onDismiss={() => setSheetOpen(false)}
        onRequest={requestMeals}
      />
    </Page>
  )
}
