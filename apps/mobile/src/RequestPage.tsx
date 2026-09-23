import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router'
import { jobLabel } from './jobLabel'
import { MealCard } from './MealCard'
import { jobMealsQuery, useJobs, useRequestMeals } from './queries'
import type { Job } from './types'

export function RequestPage() {
  const { jobId = '' } = useParams()
  const jobs = useJobs()
  const job = jobs.data?.find((j) => j.id === jobId)

  return (
    <>
      <Link to="/" className="link">
        ← Requests
      </Link>
      {job ? (
        <>
          <div className="section-header">
            <h3>{jobLabel(job)}</h3>
            {job.status !== 'succeeded' && (
              <button className="link" onClick={() => void jobs.refetch()}>
                Refresh
              </button>
            )}
          </div>
          <RequestBody job={job} />
        </>
      ) : jobs.isPending ? (
        <div className="skeleton card" />
      ) : jobs.isError ? (
        <p className="error">Could not load this request: {jobs.error.message}</p>
      ) : (
        <p className="muted">This request isn't in your recent requests.</p>
      )}
    </>
  )
}

function RequestBody({ job }: { job: Job }) {
  const navigate = useNavigate()
  const request = useRequestMeals()
  const meals = useQuery({ ...jobMealsQuery(job.id), enabled: job.status === 'succeeded' })

  if (job.status === 'pending') {
    return (
      <p className="muted">
        Waiting for the chef to pick up this request. The chef checks for new requests every 30
        minutes; this page updates on its own.
      </p>
    )
  }
  if (job.status === 'running') return <p className="muted">The chef is creating your meals…</p>
  if (job.status === 'failed') {
    return (
      <>
        <p className="error">Something went wrong creating these meals.</p>
        {request.isError && <p className="error">{request.error.message}</p>}
        <button
          disabled={request.isPending}
          onClick={() =>
            request.mutate(job.params.count, { onSuccess: () => void navigate('/') })
          }
        >
          Request again
        </button>
        <details className="muted">
          <summary>Details</summary>
          <p>{job.error}</p>
        </details>
      </>
    )
  }
  if (meals.isError) return <p className="error">Could not load meals: {meals.error.message}</p>
  if (meals.isPending) return <div className="skeleton card" />
  return (
    <>
      {meals.data.map((meal) => (
        <MealCard key={meal.id} meal={meal} />
      ))}
    </>
  )
}
