import { useIonAlert } from '@ionic/react'
import { useMemo, useRef, useState } from 'react'
import type { GroceryOrder } from '../../lib/api/types'
import { localDate, toLocalDate } from '../../lib/dates'
import { useNow } from '../../lib/useNow'
import { PlateArt } from '../../ui/art'
import { Button } from '../../ui/Button'
import { DateBar } from '../../ui/DateBar'
import { EmptyState } from '../../ui/EmptyState'
import { Notice } from '../../ui/Notice'
import { Page } from '../../ui/Page'
import { Skeleton } from '../../ui/Skeleton'
import { suggestions } from '../orders/batches'
import { DraftCard } from '../orders/DraftCard'
import { plural } from '../orders/format'
import { OrderFlowModal } from '../orders/OrderFlow'
import { useDiscardOrder, useDraft } from '../orders/orders'
import { useSession } from '../session/session'
import { daysOfWeek, formatWeekRange, weekBar } from './week'
import styles from './WeekPage.module.css'

// How often the page checks whether the phone's calendar day has changed.
const DAY_CHECK_MS = 60_000

/** This week, and the way into ordering: start an order, or continue or discard the household's draft. */
export function WeekPage() {
  // Checked every minute, so the week moves on at midnight while the tab stays open.
  const day = localDate(useNow(DAY_CHECK_MS))
  const today = useMemo(() => toLocalDate(day), [day])
  const days = useMemo(() => daysOfWeek(today), [today])
  const bar = useMemo(() => weekBar(today), [today])
  const page = useRef<HTMLElement>(null)
  const [presentingElement, setPresentingElement] = useState<HTMLElement>()
  const [flowOpen, setFlowOpen] = useState(false)
  const draft = useDraft()
  const discard = useDiscardOrder()
  const userId = useSession().data?.user.id
  const [presentAlert] = useIonAlert()

  function openFlow() {
    setPresentingElement(page.current ?? undefined)
    setFlowOpen(true)
  }

  // Discarding affects everyone in the household, so it asks first.
  function confirmDiscard(order: GroceryOrder) {
    const ideas = suggestions(order).length
    const picks = order.meal_ids.length
    const parts: string[] = []
    if (ideas) parts.push(plural(ideas, 'meal idea'))
    if (picks) parts.push(plural(picks, 'pick'))
    void presentAlert({
      header: `Discard the Order ${order.number} draft?`,
      message: `${parts.length ? `Its ${parts.join(' and ')} are` : 'It is'} deleted for everyone in the household.`,
      buttons: [
        { text: 'Cancel', role: 'cancel' },
        { text: 'Discard draft', role: 'destructive', handler: () => discard.mutate({ orderId: order.id }) },
      ],
    })
  }

  return (
    <Page title="This week" largeTitle pageRef={page} onRefresh={() => draft.refetch()}>
      <p className={styles.range}>{formatWeekRange(days)}</p>
      <DateBar days={bar} selected={day} label="This week" scrollable={false} />

      {draft.isError && (
        <div className={styles.notice}>
          <Notice action={<button onClick={() => void draft.refetch()}>Try again</button>}>
            {draft.data === undefined ? "Couldn't load your order" : "Couldn't refresh"}: {draft.error.message}
          </Notice>
        </div>
      )}
      {discard.isError && (
        <div className={styles.notice}>
          <Notice>Couldn't discard the draft: {discard.error.message}</Notice>
        </div>
      )}

      {draft.isPending && !draft.isError && (
        <div className={styles.notice}>
          <Skeleton height={220} />
        </div>
      )}
      {draft.data === null && (
        <EmptyState
          art={<PlateArt />}
          title="Nothing planned yet"
          action={<Button onClick={openFlow}>Start an order</Button>}
        >
          Pick a few easy dinners and choose when the groceries arrive.
        </EmptyState>
      )}
      {draft.data && (
        <DraftCard
          order={draft.data}
          userId={userId}
          onContinue={openFlow}
          onDiscard={() => draft.data && confirmDiscard(draft.data)}
          discarding={discard.isPending}
        />
      )}

      <OrderFlowModal isOpen={flowOpen} onDismiss={() => setFlowOpen(false)} presentingElement={presentingElement} />
    </Page>
  )
}
