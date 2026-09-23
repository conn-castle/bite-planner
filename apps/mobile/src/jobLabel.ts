import type { Job } from './types'

/** "2 meals · Sep 23, 12:06 AM" */
export function jobLabel(job: Job): string {
  const { count } = job.params
  const when = new Date(job.created_at).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
  return `${count} ${count === 1 ? 'meal' : 'meals'} · ${when}`
}
