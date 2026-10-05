import { placedMealPath } from '../meals/meals'
import { useIonRouter } from '@ionic/react'
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import type { ISODate, NextOrder, PlacedOrder } from '../../lib/api/types'
import { formatDate } from '../../lib/dates'
import { plural, theirName } from '../../lib/format'
import { useToday } from '../../lib/useNow'
import { Chip } from '../../ui/Chip'
import { Group, Row } from '../../ui/Group'
import { Notice } from '../../ui/Notice'
import { OrderCard } from '../../ui/OrderCard'
import { NavPage } from '../../ui/Page'
import { Skeleton } from '../../ui/Skeleton'
import { MealRow } from '../meals/MealRow'
import { BOOK_STATE_ICON, BOOK_STATE_LABEL, changeable, needsAttention } from '../orders/booking'
import { BookingGroup } from '../orders/BookingGroup'
import { listCounts } from '../orders/list'
import { OrderFlowModal } from '../orders/OrderFlow'
import { lockedByOther } from '../orders/lock'
import { useNextOrder, usePlacedOrders } from '../orders/orders'
import { useSession } from '../session/session'
import { byWhom, coverage, coveringFrom, followingOrder, placedOn, placedTitle } from './home'
import styles from './OrderPage.module.css'
import { PlacedTrack } from './PlacedCard'

/** The tab an order is opened in: Orders (from Home) or History. Its pages are pushed under the tab's path. */
export interface OrderTab {
  href: '/week' | '/history'
  backLabel: string
}

/**
 * A placed order, read-only, opened from Home or History: its progress,
 * meals, dates, and the order after it.
 */
export function OrderPage({ tab }: { tab: OrderTab }) {
  const { orderId } = useParams<{ orderId: string }>()
  const today = useToday()
  // The list the tab it's opened from already has: Home's recent orders, or History's.
  const placed = usePlacedOrders(tab.href === '/week' ? coveringFrom(today) : undefined)
  const next = useNextOrder()
  const order = placed.data?.find((each) => each.id === orderId)
  const router = useIonRouter()
  // The flow: for the next order, or for this one, back in the draft to change it
  // (it then leaves the placed orders; closing the flow goes back).
  const [flow, setFlow] = useState<Flow | null>(null)

  return (
    <NavPage title={order ? `Order ${order.number}` : 'Order'} tab={tab}>
      {placed.isError && (
        <Notice action={<button onClick={() => void placed.refetch()}>Try again</button>}>
          Couldn't load this order: {placed.error.message}
        </Notice>
      )}
      {placed.isPending && !placed.isError && <Skeleton height={220} radius={26} />}
      {placed.data && !order && !flow?.orderId && <Notice>No such order.</Notice>}
      {order && (
        <PlacedOrderView
          order={order}
          placed={placed.data ?? []}
          next={next.data}
          today={today}
          base={tab.href}
          onOpenFlow={setFlow}
        />
      )}
      <OrderFlowModal
        isOpen={flow !== null}
        orderId={flow?.orderId}
        startAt={flow?.startAt}
        onDismiss={() => {
          if (flow?.orderId) router.goBack()
          setFlow(null)
        }}
      />
    </NavPage>
  )
}

/** What the flow opens: the next order, or this one to change (at a step). */
interface Flow {
  orderId?: string
  startAt?: 'delivery' | 'list'
}

interface PlacedOrderViewProps {
  order: PlacedOrder
  placed: PlacedOrder[]
  next: NextOrder | undefined
  today: ISODate
  /** The tab's path, which the order's meals and the next order are pushed under. */
  base: OrderTab['href']
  onOpenFlow: (flow: Flow) => void
}

function PlacedOrderView({ order, placed, next, today, base, onOpenFlow }: PlacedOrderViewProps) {
  const userId = useSession().data?.user.id
  const router = useIonRouter()
  const meals = plural(order.meals.length, 'meal')
  const following = followingOrder(order, placed, next)
  const theirLock = lockedByOther(next?.draft, userId)
  const state = order.book_state

  function openFlow() {
    onOpenFlow({})
  }

  return (
    <div>
      <OrderCard
        number={order.number}
        look="placed"
        chip={
          <Chip icon={BOOK_STATE_ICON[state]} tone={needsAttention(state) ? 'danger' : 'default'}>
            {BOOK_STATE_LABEL[state]}
          </Chip>
        }
        title={placedTitle(order, false, placed, today)}
        subtitle={`${coverage(order)} · ${meals}`}
      >
        <div className={styles.track}>
          <PlacedTrack order={order} today={today} userId={userId} dated />
        </div>
      </OrderCard>

      <BookingGroup
        order={order}
        onChanged={(step) => onOpenFlow({ orderId: order.id, startAt: step })}
        onCancelled={() => router.goBack()}
      />

      <Group
        title="Meals"
        footerIcon="cal"
        footer={`These meals cover ${coverage(order)}. Cook them in any order.`}
      >
        {order.meals.map((meal) => (
          <MealRow
            key={meal.id}
            meal={meal}
            servings={meal.servings}
            onOpen={() => router.push(placedMealPath(base, meal))}
            rated
          />
        ))}
      </Group>

      <Group
        title="Delivery"
        footerIcon="lock"
        footer={changeable(state) ? 'To change it, change the order (above).' : 'Placed orders are read-only.'}
      >
        <Row label="Delivery date" detail={formatDate(order.delivery_date, 'medium')} />
        <Row label="Next delivery" detail={formatDate(order.next_delivery_date, 'medium')} />
        {order.shopping_list.length > 0 && (
          <Row
            label="Shopping list"
            detail={listDetail(order)}
            chevron
            onClick={() => router.push(`${base}/orders/${order.id}/list`)}
          />
        )}
        <Row label="Placed" detail={`${formatDate(placedOn(order), 'medium')} · ${byWhom(order.placed_by, userId)}`} />
      </Group>

      {following?.kind === 'placed' && (
        <Group title="Next order">
          <Row
            label={`Order ${following.order.number} · delivery ${formatDate(following.order.delivery_date, 'medium')}`}
            sub="Order placed"
            chevron
            onClick={() => router.push(`${base}/orders/${following.order.id}`)}
          />
        </Group>
      )}
      {following?.kind === 'next' && following.next.target_date && (
        <Group title="Next order">
          <Row
            label={`Order ${following.next.number} · delivery ${formatDate(following.next.target_date, 'medium')}`}
            sub={
              theirLock
                ? `${theirName(theirLock.member)} is placing it`
                : following.next.decide_by_date
                  ? `Decide by ${formatDate(following.next.decide_by_date, 'medium')}`
                  : undefined
            }
            chevron
            onClick={openFlow}
          />
        </Group>
      )}

    </div>
  )
}

/** "12 items · 2 removed": what an order's list ordered. */
function listDetail(order: PlacedOrder): string {
  const { ordered, removed } = listCounts(order.shopping_list)
  return removed > 0 ? `${plural(ordered, 'item')} · ${removed} removed` : plural(ordered, 'item')
}
