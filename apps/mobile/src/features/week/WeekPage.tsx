import { placedMealPath } from '../meals/meals'
import { useIonAlert, useIonRouter } from '@ionic/react'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { GroceryOrder, ISODate, NextOrder, PlacedMeal, PlacedOrder } from '../../lib/api/types'
import { addDays, formatDate, localDate, toLocalDate } from '../../lib/dates'
import { apiRequest } from '../../lib/api/client'
import { ApiError } from '../../lib/api/errors'
import { errorFields, logger } from '../../lib/log'
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
import { OrderFlowModal } from '../orders/OrderFlow'
import { nextQuery, useDiscardOrder, useNextOrder, usePlacedOrders } from '../orders/orders'
import { clearTapped, type Delivered, tapResult, useTapped, useYourReminder } from '../notifications/notifications'
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
  const page = useRef<HTMLElement>(null)
  const [presentingElement, setPresentingElement] = useState<HTMLElement>()
  const [flowOpen, setFlowOpen] = useState(false)
  const next = useNextOrder()
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

  function openFlow() {
    setPresentingElement(page.current ?? undefined)
    setFlowOpen(true)
  }

  // A tapped notification lands here: with fresh data, open the draft it's about,
  // if it's still the one to work on. Otherwise Home stays, with a note saying
  // why (its top card has the next step); nothing starts a draft without a tap
  // in the app.
  const tapped = useTapped()
  useEffect(() => {
    if (!tapped || tapped.kind === 'test') return
    let current = true
    async function act(about: Exclude<Delivered, { kind: 'test' }>) {
      const fresh = await queryClient.fetchQuery({ ...nextQuery, staleTime: 0 })
      const tappedOrder =
        about.kind === 'ideas_ready' && fresh.draft?.id !== about.orderId
          ? await apiRequest<GroceryOrder>('GET', `/v1/grocery-orders/${about.orderId}`).catch((error: unknown) => {
              if (error instanceof ApiError && error.status === 404) return 'discarded' as const
              throw error
            })
          : null
      return tapResult(about, fresh, tappedOrder)
    }
    act(tapped)
      .then((result) => {
        if (!current) return
        logger.info('push.tap_opened', { kind: tapped.kind, open: result.open })
        clearTapped()
        if (result.open) openFlow()
        else void presentAlert({ header: result.header, message: result.message, buttons: ['OK'] })
      })
      .catch((error: unknown) => {
        if (!current) return
        logger.warn('push.tap_failed', errorFields(error))
        clearTapped()
        void presentAlert({
          header: "Couldn't open the order",
          message: error instanceof Error ? error.message : String(error),
          buttons: ['OK'],
        })
      })
    return () => {
      current = false
    }
  }, [tapped, queryClient, presentAlert])

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
        <IconButton variant="white" aria-label={next.data?.draft ? 'Continue order' : 'New order'} onClick={openFlow}>
          <Icon name="plus" />
        </IconButton>
      }
      pageRef={page}
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
          onStart={openFlow}
          onDiscard={confirmDiscard}
          discarding={discard.isPending}
          onOpenOrder={(orderId) => router.push(`/week/orders/${orderId}`)}
          onOpenMeal={(meal) => router.push(placedMealPath('/week', meal))}
        />
      )}

      <OrderFlowModal isOpen={flowOpen} onDismiss={() => setFlowOpen(false)} presentingElement={presentingElement} />
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
  const cards = useMemo(() => homeCards(placed, next, today), [placed, next, today])
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
