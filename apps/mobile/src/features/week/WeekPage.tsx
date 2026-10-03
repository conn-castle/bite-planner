import { placedMealPath } from '../meals/meals'
import { useIonAlert, useIonRouter, useIonViewDidEnter, useIonViewDidLeave } from '@ionic/react'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { GroceryOrder, ISODate, NextOrder, PlacedMeal, PlacedOrder } from '../../lib/api/types'
import { addDays, formatDate, localDate, toLocalDate } from '../../lib/dates'
import { errorFields, logger } from '../../lib/log'
import { queryKeys } from '../../lib/query/keys'
import { useNow } from '../../lib/useNow'
import { IconButton } from '../../ui/Button'
import { CardStack } from '../../ui/CardStack'
import { DateBar, type DayCaption } from '../../ui/DateBar'
import { Icon } from '../../ui/Icon'
import { Notice } from '../../ui/Notice'
import { Page } from '../../ui/Page'
import { Skeleton } from '../../ui/Skeleton'
import { suggestions } from '../orders/batches'
import { DraftCard } from '../orders/DraftCard'
import { plural } from '../orders/format'
import { LOCK_RENEW_MS, lockedByOther } from '../orders/lock'
import { LockedCard } from '../orders/LockedCard'
import { OrderFlowModal } from '../orders/OrderFlow'
import { nextQuery, useDiscardOrder, useNextOrder, usePlacedOrders } from '../orders/orders'
import {
  awaitIdeas,
  clearTapped,
  onTap,
  resolveTap,
  stopAwaitingIdeas,
  type Tap,
  useYourReminder,
} from '../notifications/notifications'
import { useSession } from '../session/session'
import { coveringFrom, dayCaption, homeCards, homeStrip } from './home'
import { NextCard } from './NextCard'
import { PlacedCard } from './PlacedCard'
import styles from './WeekPage.module.css'

// How often the page checks whether the phone's calendar day has changed.
const DAY_CHECK_MS = 60_000

/**
 * Orders (Home): the days around today and the orders covering them, then the
 * orders as cards, the next step first: start or continue the next order, or
 * follow a placed one.
 */
export function WeekPage() {
  // Checked every minute, so Home moves on at midnight while the tab stays open.
  const today = localDate(useNow(DAY_CHECK_MS))
  const queryClient = useQueryClient()
  const shownDay = useRef(today)
  const [flowOpen, setFlowOpen] = useState(false)
  // While Home shows, the next order is checked every 30 seconds: someone else
  // may start ordering, stop, or place it.
  const [shown, setShown] = useState(true)
  useIonViewDidEnter(() => setShown(true))
  useIonViewDidLeave(() => setShown(false))
  const next = useNextOrder(shown ? LOCK_RENEW_MS : false)
  const placed = usePlacedOrders(coveringFrom(today))
  const discard = useDiscardOrder()
  const router = useIonRouter()
  const [presentAlert] = useIonAlert()

  // On a new day the next order's earliest delivery date moves on too (the
  // placed orders follow on their own: their query is keyed by the day).
  useEffect(() => {
    if (shownDay.current === today) return
    shownDay.current = today
    void queryClient.invalidateQueries({ queryKey: nextQuery.queryKey })
  }, [today, queryClient])

  // Someone placed the next order (its number moved on): its card joins the placed ones.
  const nextNumber = next.data?.number
  const seenNumber = useRef(nextNumber)
  useEffect(() => {
    if (seenNumber.current !== undefined && nextNumber !== undefined && nextNumber !== seenNumber.current) {
      void queryClient.invalidateQueries({ queryKey: queryKeys.placedOrders })
    }
    seenNumber.current = nextNumber
  }, [nextNumber, queryClient])

  // The order the flow opens: a notification's, or (undefined) the household's draft, made if there's none.
  const [flowOrderId, setFlowOrderId] = useState<string>()
  // Where a notification's flow opens: its shopping list, for "list ready".
  const [flowStartAt, setFlowStartAt] = useState<'list'>()
  function openFlow(orderId?: string, startAt?: 'list') {
    setFlowOrderId(orderId)
    setFlowStartAt(startAt)
    setFlowOpen(true)
  }

  // A tapped notification lands here: with fresh data, open the draft it's about,
  // if it's still the one to work on. Otherwise Home stays, with a note saying
  // why (its top card has the next step); nothing starts a draft without a tap
  // in the app. "Ideas ready" for the draft already cached opens it at once,
  // loading its ideas, rather than waiting on the phone's connection.
  useEffect(() => {
    // The tap being handled. A newer one supersedes it; once Home has gone,
    // none is, and the tap stays for the next Home to handle.
    let handling: Tap | null = null
    const stop = onTap((tapped) => {
      if (tapped.about.kind === 'test') return
      handling = tapped
      if (tapped.about.kind === 'booking') {
        // How booking went: that order's page says, with what can be done.
        clearTapped()
        logger.info('push.tap_opened', { kind: 'booking', open: true })
        void queryClient.invalidateQueries({ queryKey: queryKeys.placedOrders })
        router.push(`/week/orders/${tapped.about.orderId}`)
        return
      }
      const about = tapped.about
      const current = () => handling === tapped
      const since = (at: number) => Math.round(Date.now() - at)
      const cachedDraft = queryClient.getQueryData<NextOrder>(nextQuery.queryKey)?.draft
      const early = about.kind === 'ideas_ready' && cachedDraft?.id === about.orderId
      if (early) {
        awaitIdeas(queryClient, about.orderId, tapped.at)
        openFlow(about.orderId)
        logger.info('push.tap_opened', { kind: about.kind, open: true, early: true, ms: since(tapped.at) })
      }
      resolveTap(about)
        .then((result) => {
          if (!current()) return
          clearTapped()
          if (early && result.open) return
          logger.info('push.tap_opened', { kind: about.kind, open: result.open, early, ms: since(tapped.at) })
          if (result.open) {
            // The list the tap is about may be newer than the copy cached.
            if (result.step === 'list') void queryClient.invalidateQueries({ queryKey: queryKeys.order(result.orderId) })
            openFlow(result.orderId, result.step)
            return
          }
          // Opened early, but the draft is gone or placed: close it and say why.
          if (early) {
            stopAwaitingIdeas('closed')
            setFlowOpen(false)
          }
          void presentAlert({ header: result.header, message: result.message, buttons: ['OK'] })
        })
        .catch((error: unknown) => {
          if (!current()) return
          logger.warn('push.tap_failed', { ...errorFields(error), ms: since(tapped.at) })
          clearTapped()
          // Opened early, the flow shows its own problem (and keeps trying).
          if (early) return
          void presentAlert({
            header: "Couldn't open the order",
            message: error instanceof Error ? error.message : String(error),
            buttons: ['OK'],
          })
        })
    })
    return () => {
      handling = null
      stop()
    }
  }, [queryClient, presentAlert, router])

  // Discarding affects everyone in the household, so it asks first.
  function confirmDiscard(order: GroceryOrder) {
    const ideas = suggestions(order).length
    const picks = order.picks.length
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

  const failed = next.isError ? next : placed.isError ? placed : null
  const loaded = next.data !== undefined && placed.data !== undefined

  return (
    <Page
      title="Orders"
      overline={toLocalDate(today).toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}
      actions={
        // Opens the flow, which continues the household's draft or starts one.
        <IconButton variant="white" aria-label={next.data?.draft ? 'Continue order' : 'New order'} onClick={() => openFlow()}>
          <Icon name="plus" />
        </IconButton>
      }
      onRefresh={() => Promise.all([next.refetch(), placed.refetch()])}
    >
      {failed?.error && (
        <div className={styles.notice}>
          <Notice
            action={<button onClick={() => void Promise.all([next.refetch(), placed.refetch()])}>Try again</button>}
          >
            {loaded ? "Couldn't refresh" : "Couldn't load your orders"}: {failed.error.message}
          </Notice>
        </div>
      )}
      {discard.isError && (
        <div className={styles.notice}>
          <Notice>Couldn't discard the draft: {discard.error.message}</Notice>
        </div>
      )}

      {!loaded && !failed && (
        <div className={styles.loading}>
          <Skeleton height={112} radius={20} />
          <Skeleton height={220} radius={26} />
        </div>
      )}
      {next.data && placed.data && (
        <Home
          // A new day starts over: the strip around it, today highlighted, the cards folded as they start.
          key={today}
          next={next.data}
          placed={placed.data}
          today={today}
          onStart={() => openFlow()}
          onDiscard={confirmDiscard}
          discarding={discard.isPending}
          onOpenOrder={(orderId) => router.push(`/week/orders/${orderId}`)}
          onOpenMeal={(meal) => router.push(placedMealPath('/week', meal))}
        />
      )}

      <OrderFlowModal
        isOpen={flowOpen}
        onDismiss={() => setFlowOpen(false)}
        orderId={flowOrderId}
        startAt={flowStartAt}
      />
    </Page>
  )
}

interface HomeProps {
  next: NextOrder
  placed: PlacedOrder[]
  today: ISODate
  onStart: () => void
  onDiscard: (draft: GroceryOrder) => void
  discarding: boolean
  onOpenOrder: (orderId: string) => void
  onOpenMeal: (meal: PlacedMeal) => void
}

function Home({ next, placed, today, onStart, onDiscard, discarding, onOpenOrder, onOpenMeal }: HomeProps) {
  const userId = useSession().data?.user.id
  const [chosenDay, setChosenDay] = useState<ISODate | null>(null)
  // Cards whose meals were folded or unfolded from how they start (the top card open, the rest folded).
  const [toggled, setToggled] = useState<ReadonlySet<string>>(new Set())
  const days = useMemo(() => homeStrip(placed, next, today), [placed, next, today])
  const cards = useMemo(() => homeCards(placed, next, today, userId), [placed, next, today, userId])
  const theirLock = lockedByOther(next.draft, userId)
  const shownDay = chosenDay ?? today

  const reminder = useYourReminder()
  const { order, lines } = dayCaption(placed, next, shownDay, today, reminder.time)
  const caption: DayCaption = {
    day: shownDay === today ? 'Today' : shownDay === addDays(today, 1) ? 'Tomorrow' : formatDate(shownDay, 'medium'),
    order,
    text: lines.join(' · '),
  }

  function toggle(orderId: string) {
    setToggled((current) => {
      const updated = new Set(current)
      if (!updated.delete(orderId)) updated.add(orderId)
      return updated
    })
  }

  return (
    <>
      <DateBar days={days} selected={shownDay} onChoose={setChosenDay} label="Days" compact caption={caption} />
      <CardStack className={styles.stack}>
        {cards.map((card) =>
          card.kind === 'placed' ? (
            <PlacedCard
              key={card.order.id}
              order={card.order}
              top={card.top}
              placed={placed}
              next={next}
              today={today}
              userId={userId}
              mealsOpen={card.top !== toggled.has(card.order.id)}
              onToggleMeals={() => toggle(card.order.id)}
              onOpen={() => onOpenOrder(card.order.id)}
              onOpenMeal={onOpenMeal}
            />
          ) : next.draft && theirLock ? (
            <LockedCard key="next" order={next.draft} lock={theirLock} />
          ) : next.draft ? (
            <DraftCard
              key="next"
              order={next.draft}
              userId={userId}
              today={today}
              onContinue={onStart}
              onDiscard={() => next.draft && onDiscard(next.draft)}
              discarding={discarding}
            />
          ) : (
            <NextCard key="next" next={next} placed={placed} today={today} top={card.top} onStart={onStart} />
          ),
        )}
      </CardStack>
    </>
  )
}
