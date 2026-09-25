import { useIonRouter } from '@ionic/react'
import { useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import type { ISODate, NextOrder, PlacedOrder } from '../../lib/api/types'
import { formatDate, localDate } from '../../lib/dates'
import { useNow } from '../../lib/useNow'
import { MealArt } from '../../ui/art'
import { Chip } from '../../ui/Chip'
import { Group, ListRow, Row } from '../../ui/Group'
import { Notice } from '../../ui/Notice'
import { OrderCard } from '../../ui/OrderCard'
import { NavPage } from '../../ui/Page'
import { Skeleton } from '../../ui/Skeleton'
import { formatMinutes } from '../meals/facts'
import { plural } from '../orders/format'
import { OrderFlowModal } from '../orders/OrderFlow'
import { useNextOrder, usePlacedOrders } from '../orders/orders'
import { useSession } from '../session/session'
import { byWhom, coverage, coveringFrom, followingOrder, placedOn, placedTitle } from './home'
import styles from './OrderPage.module.css'
import { PlacedTrack } from './PlacedCard'

// How often the page checks whether the phone's calendar day has changed.
const DAY_CHECK_MS = 60_000

/**
 * A placed order, read-only, opened from Home: its progress, meals, dates, and
 * the order after it. It shows the order as Home has it.
 */
export function OrderPage() {
  const { orderId } = useParams<{ orderId: string }>()
  const today = localDate(useNow(DAY_CHECK_MS))
  const placed = usePlacedOrders(coveringFrom(today))
  const next = useNextOrder()
  const order = placed.data?.find((each) => each.id === orderId)

  return (
    <NavPage title={order ? `Order ${order.number}` : 'Order'} tab={{ href: '/week', backLabel: 'Orders' }}>
      {placed.isError && (
        <Notice action={<button onClick={() => void placed.refetch()}>Try again</button>}>
          Couldn't load this order: {placed.error.message}
        </Notice>
      )}
      {placed.isPending && !placed.isError && <Skeleton height={220} radius={26} />}
      {placed.data && !order && <Notice>This order is no longer on Orders.</Notice>}
      {order && <PlacedOrderView order={order} placed={placed.data ?? []} next={next.data} today={today} />}
    </NavPage>
  )
}

interface PlacedOrderViewProps {
  order: PlacedOrder
  placed: PlacedOrder[]
  next: NextOrder | undefined
  today: ISODate
}

function PlacedOrderView({ order, placed, next, today }: PlacedOrderViewProps) {
  const userId = useSession().data?.user.id
  const router = useIonRouter()
  const page = useRef<HTMLDivElement>(null)
  const [presentingElement, setPresentingElement] = useState<HTMLElement>()
  const [flowOpen, setFlowOpen] = useState(false)
  const meals = plural(order.meals.length, 'meal')
  const following = followingOrder(order, placed, next)

  function openFlow() {
    setPresentingElement(page.current?.closest<HTMLElement>('.ion-page') ?? undefined)
    setFlowOpen(true)
  }

  return (
    <div ref={page}>
      <OrderCard
        number={order.number}
        look="placed"
        chip={<Chip icon="bag">Order placed</Chip>}
        title={placedTitle(order, false, placed, today)}
        subtitle={`${coverage(order)} · ${meals}`}
      >
        <div className={styles.track}>
          <PlacedTrack order={order} today={today} userId={userId} dated />
        </div>
      </OrderCard>

      <Group
        title="Meals"
        footerIcon="cal"
        footer={`These meals cover ${coverage(order)}. Cook them in any order.`}
      >
        {order.meals.map((meal) => {
          const minutes = meal.total_time_minutes ?? meal.active_time_minutes
          return (
            <ListRow
              key={meal.id}
              art={<MealArt />}
              overline={minutes != null ? formatMinutes(minutes) : undefined}
              label={meal.name}
              onClick={() => router.push(`/week/meals/${meal.id}`)}
            />
          )
        })}
      </Group>

      <Group title="Delivery" footerIcon="lock" footer="Placed orders are read-only.">
        <Row label="Delivery date" detail={formatDate(order.delivery_date, 'medium')} />
        <Row label="Next delivery" detail={formatDate(order.next_delivery_date, 'medium')} />
        <Row label="Placed" detail={`${formatDate(placedOn(order), 'medium')} · ${byWhom(order.placed_by, userId)}`} />
      </Group>

      {following?.kind === 'placed' && (
        <Group title="Next order">
          <Row
            label={`Order ${following.order.number} · delivery ${formatDate(following.order.delivery_date, 'medium')}`}
            sub="Order placed"
            chevron
            onClick={() => router.push(`/week/orders/${following.order.id}`)}
          />
        </Group>
      )}
      {following?.kind === 'next' && following.next.target_date && (
        <Group title="Next order">
          <Row
            label={`Order ${following.next.number} · delivery ${formatDate(following.next.target_date, 'medium')}`}
            sub={
              following.next.decide_by_date
                ? `Decide by ${formatDate(following.next.decide_by_date, 'medium')}`
                : undefined
            }
            chevron
            onClick={openFlow}
          />
        </Group>
      )}

      <OrderFlowModal isOpen={flowOpen} onDismiss={() => setFlowOpen(false)} presentingElement={presentingElement} />
    </div>
  )
}
