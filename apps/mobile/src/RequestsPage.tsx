import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { jobLabel } from './jobLabel'
import { jobMealsQuery, OPTIMISTIC_PREFIX, useJobs, useRequestMeals } from './queries'

// Recent finished requests whose meals are fetched ahead of a tap.
const PREFETCH_COUNT = 5

export function RequestsPage() {
  const queryClient = useQueryClient()
  const jobs = useJobs()
  const request = useRequestMeals()
  // Kept as text so the field can be cleared while typing.
  const [countText, setCountText] = useState('5')
  const count = /^\d+$/.test(countText) ? Number(countText) : null
  const countIsValid = count !== null && count >= 1 && count <= 14

  // Opening a finished request should be instant.
  useEffect(() => {
    for (const job of (jobs.data ?? []).filter((j) => j.status === 'succeeded').slice(0, PREFETCH_COUNT)) {
      void queryClient.prefetchQuery(jobMealsQuery(job.id))
    }
  }, [jobs.data, queryClient])

  function submit(event: React.FormEvent) {
    event.preventDefault()
    if (count !== null && countIsValid) request.mutate(count)
  }

  return (
    <>
      <form onSubmit={submit} className="card">
        <label htmlFor="count">Meals</label>
        <input
          id="count"
          type="number"
          inputMode="numeric"
          min={1}
          max={14}
          value={countText}
          onChange={(event) => setCountText(event.target.value)}
        />
        <button type="submit" disabled={request.isPending || !countIsValid}>
          Request meals
        </button>
        {!countIsValid && <p className="muted hint">Choose 1 to 14 meals.</p>}
      </form>
      {request.isError && <p className="error">{request.error.message}</p>}

      <div className="section-header">
        <h3>Requests</h3>
        {jobs.isFetching && <span className="muted">Updating…</span>}
      </div>
      {jobs.isError && (
        <p className="error">
          Could not load requests: {jobs.error.message}{' '}
          <button className="link" onClick={() => void jobs.refetch()}>
            Try again
          </button>
        </p>
      )}
      {jobs.isPending && !jobs.isError && (
        <>
          <div className="skeleton job" />
          <div className="skeleton job" />
          <div className="skeleton job" />
        </>
      )}
      {jobs.data?.length === 0 && <p className="muted">No requests yet.</p>}
      {jobs.data?.map((job) =>
        job.id.startsWith(OPTIMISTIC_PREFIX) ? (
          <div key={job.id} className="job sending">
            <span>{jobLabel(job)}</span>
            <span className="muted">sending…</span>
          </div>
        ) : (
          <Link
            key={job.id}
            to={`/requests/${job.id}`}
            className="job"
            onPointerDown={() => {
              if (job.status === 'succeeded') void queryClient.prefetchQuery(jobMealsQuery(job.id))
            }}
          >
            <span>{jobLabel(job)}</span>
            <span className={`status-${job.status}`}>{job.status}</span>
          </Link>
        ),
      )}
    </>
  )
}
