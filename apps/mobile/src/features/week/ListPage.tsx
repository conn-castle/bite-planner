import { useParams } from 'react-router-dom'
import { localDate } from '../../lib/dates'
import { useNow } from '../../lib/useNow'
import { Note } from '../../ui/Note'
import { Notice } from '../../ui/Notice'
import { NavPage } from '../../ui/Page'
import { ShoppingList } from '../../ui/ShoppingList'
import { Skeleton } from '../../ui/Skeleton'
import { Stats } from '../../ui/Stats'
import { forMeals, itemAmount, listCounts, splitList } from '../orders/list'
import { usePlacedOrders } from '../orders/orders'
import { coveringFrom } from './home'
import styles from './ListPage.module.css'
import type { OrderTab } from './OrderPage'

// How often the page checks whether the phone's calendar day has changed.
const DAY_CHECK_MS = 60_000

/** A placed order's shopping list, read-only: what was ordered, and what was taken off. */
export function ListPage({ tab }: { tab: OrderTab }) {
  const { orderId } = useParams<{ orderId: string }>()
  const today = localDate(useNow(DAY_CHECK_MS))
  // The list the tab it's opened from already has: Home's recent orders, or History's.
  const placed = usePlacedOrders(tab.href === '/week' ? coveringFrom(today) : undefined)
  const order = placed.data?.find((each) => each.id === orderId)
  const { toBuy } = splitList(order?.shopping_list ?? [])
  const { ordered, removed } = listCounts(toBuy)
  const names = new Map(order?.meals.map((meal) => [meal.id, meal.name]))

  return (
    <NavPage title="Shopping list" tab={{ href: tab.href, backLabel: order ? `Order ${order.number}` : 'Back' }}>
      {placed.isError && (
        <Notice action={<button onClick={() => void placed.refetch()}>Try again</button>}>
          Couldn't load this order: {placed.error.message}
        </Notice>
      )}
      {placed.isPending && !placed.isError && <Skeleton height={220} radius={26} />}
      {placed.data && !order && <Notice>No such order.</Notice>}
      {order && (
        <div className={styles.list}>
          <Stats
            stats={[
              { value: ordered, label: 'items ordered' },
              { value: removed, label: 'removed' },
              { value: order.meals.length, label: order.meals.length === 1 ? 'meal' : 'meals' },
            ]}
          />
          <ShoppingList items={toBuy} amount={itemAmount} forMeals={(item) => forMeals(item, names)} />
          <Note icon="lock">Order {order.number} is placed, so its list is read-only.</Note>
        </div>
      )}
    </NavPage>
  )
}
