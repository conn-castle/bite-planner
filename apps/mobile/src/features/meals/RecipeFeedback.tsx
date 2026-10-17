import { useRef, useState, useSyncExternalStore } from 'react'
import { onlineManager } from '@tanstack/react-query'
import type { GroceryOrder, Meal, MealSummary } from '../../lib/api/types'
import { elapsed } from '../../lib/format'
import { useNow } from '../../lib/useNow'
import { Button } from '../../ui/Button'
import { InfoDialog } from '../../ui/InfoDialog'
import { Notice } from '../../ui/Notice'
import { Note } from '../../ui/Note'
import { TextField } from '../../ui/TextField'
import { MealArt } from '../../ui/art'
import { RecipeDiff } from '../../ui/RecipeDiff'
import { useOrderSession } from '../orders/lock'
import { useDecideRecipe, useEditRecipe, type EditRecipe, useEditsPending } from '../orders/orders'
import { proposalPicture, recipeDiff } from './recipeDiff'
import styles from './RecipeFeedback.module.css'

export function RecipeFeedback({ order, meal, summary }: { order: GroceryOrder; meal: Meal | undefined; summary: MealSummary }) {
  const change = order.recipe_changes.find((c) => c.meal_id === summary.id)
  const session = useOrderSession()
  const send = useEditRecipe()
  const online = useSyncExternalStore(onlineManager.subscribe, () => onlineManager.isOnline())
  const decide = useDecideRecipe()
  const saving = useEditsPending()
  const now = useNow(1000)
  const [open, setOpen] = useState(false)
  const [feedback, setFeedback] = useState('')
  // Keep a transport retry bound to its original payload and id, even after a lost response.
  const attempt = useRef<EditRecipe | null>(null)
  const editable = order.recipes_editable && order.lock?.session === session
  const ready = change?.state === 'ready' && change.proposal !== null
  function request() {
    setFeedback(change?.feedback ?? '')
    attempt.current = null
    send.reset()
    setOpen(true)
  }
  async function submit() {
    const text = feedback.trim()
    if (!text || !online || saving || send.isPending) return
    if (attempt.current === null || attempt.current.feedback !== text) {
      attempt.current = { orderId: order.id, mealId: summary.id, jobId: crypto.randomUUID(), feedback: text, replaces: change?.job_id ?? null, session }
    }
    try {
      await send.mutateAsync(attempt.current)
      setOpen(false) // only a durable 200/201 acknowledgement closes Feedback
    } catch { /* The dialog retains its text and reports the error below. */ }
  }
  const decision = (action: 'accept' | 'reject') => {
    if (!change) return
    decide.mutate({ orderId: order.id, mealId: summary.id, jobId: change.job_id, decision: action, baseRevision: change.base_revision, session })
  }
  return <section className={styles.feedback}>
    {!editable && <Note icon="info">{order.recipes_editable ? 'Open this order here to change its recipes.' : "Shopping has started, so recipes can't change on this order."}</Note>}
    {change?.state === 'running' && <div className={styles.status} role="status" aria-busy="true">
      <strong>Changing your recipe · {elapsed(new Date(change.started_at ?? change.created_at), now)}</strong>
      <p>You can keep choosing meals or close the app. Your changes will be here to review when they're ready.</p>
    </div>}
    {change?.state === 'failed' && <>
      <Notice>{change.error ?? "We couldn't change this recipe."} Your last accepted recipe is kept below.</Notice>
      <div className={styles.actions}>
        {editable && <Button variant="secondary" onClick={request} disabled={saving}>Try again</Button>}
        <Button variant="secondary" onClick={() => decision('reject')} disabled={saving || order.lock?.session !== session}>Keep current recipe</Button>
      </div>
    </>}
    {ready && change.proposal && <>
      <h2>Review changes</h2>
      <p>Compared with your last accepted recipe. These changes wait for your approval.</p>
      <blockquote>{change.feedback}</blockquote>
      {meal && meal.revision === change.base_revision ? <RecipeDiff sections={recipeDiff(meal, change.proposal)}
        currentArt={<MealArt meal={meal} />} proposedArt={<MealArt meal={{ id: meal.id, picture: proposalPicture(change.proposal) }} />} /> : <Note icon="info">Refreshing the recipe before comparing changes…</Note>}
      <div className={styles.actions}>
        <Button block onClick={() => decision('accept')} disabled={!editable || saving || !meal || meal.revision !== change.base_revision}>Accept changes</Button>
        <Button variant="secondary" onClick={() => decision('reject')} disabled={saving || order.lock?.session !== session}>Reject</Button>
        {editable && <Button variant="secondary" onClick={request} disabled={saving}>Revise</Button>}
      </div>
    </>}
    {!change && editable && <Button variant="secondary" block onClick={request} disabled={summary.rating === 'not_for_us'}>Feedback</Button>}
    {decide.isError && <Notice>Couldn't save that decision: {decide.error.message}</Notice>}
    <InfoDialog initialFocus="input" isOpen={open} onClose={() => setOpen(false)} dismissible={!send.isPending} title="Feedback" icon="spark" footer={
      <div className={styles.actions}>
        <Button variant="secondary" onClick={() => setOpen(false)} disabled={send.isPending}>Cancel</Button>
        <Button onClick={() => void submit()} disabled={!online || !feedback.trim() || saving || send.isPending}>{send.isPending ? 'Sending…' : 'Send'}</Button>
      </div>
    }>
      <p>Tell Chef what you'd like to change. You'll review the changes before using them.</p>
      <TextField multiline label="What would you like to change?" value={feedback} maxLength={500} rows={5}
        disabled={send.isPending} onChange={(event) => setFeedback(event.target.value)} />
      <small>{feedback.length} / 500</small>
      {change && <p>A new attempt starts from your last accepted recipe. The earlier request is editable here.</p>}
      {!online && <Notice>You're offline. Your text stays here; reconnect to send, or Cancel to leave.</Notice>}
      {send.isError && <Notice>Couldn't send your request: {send.error.message}</Notice>}
      {send.isPending && <p>Keep this open until your request is saved.</p>}
    </InfoDialog>
  </section>
}
