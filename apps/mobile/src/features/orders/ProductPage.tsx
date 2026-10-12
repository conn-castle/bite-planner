import { useEffect } from 'react'
import { ApiError } from '../../lib/api/errors'
import type { Product } from '../../lib/api/types'
import { haptics } from '../../lib/haptics'
import { logger } from '../../lib/log'
import { formatMoney, isWeighed, productCost, productSize } from '../../lib/products'
import { Group, Row } from '../../ui/Group'
import { ProductAlternatives } from '../../ui/ProductAlternatives'
import { Notice } from '../../ui/Notice'
import { NavPage } from '../../ui/Page'
import { BoughtBefore, ProductPicture } from '../../ui/ShoppingList'
import { Stepper } from '../../ui/Stepper'
import { Switch } from '../../ui/Switch'
import { pickedMeals } from './batches'
import { useSession } from '../session/session'
import { itemAmount, listState, mealNames, storeName } from './list'
import { lockHolder, useOrderSession } from './lock'
import { useAcquiringOrder, useOrder, useSetItem, useSetItemError } from './orders'
import styles from './ProductPage.module.css'

// The most of a product the stepper goes to.
export const MAX_COUNT = 99

interface ProductPageProps {
  orderId: string
  itemId: string
  view?: 'product' | 'more-alternatives'
  onMoreAlternatives?: () => void
  /** Back to the list, once an alternative is used. */
  onDone: () => void
  /** Back to the flow, when the page no longer applies (the flow then shows why). */
  onLeave: () => void
}

/**
 * One item of the shopped list, pushed onto the flow's nav: its product (Buy,
 * How many, what it costs and is for) and its alternatives, each to use instead.
 * For an item the store doesn't have: why the meals may work without it, Skip,
 * and its alternatives.
 */
export function ProductPage({ orderId, itemId, view = 'product', onMoreAlternatives, onDone, onLeave }: ProductPageProps) {
  const session = useOrderSession()
  const userId = useSession().data?.user.id
  const shown = useOrder(orderId, session)
  const acquiring: boolean = useAcquiringOrder(orderId, session)
  const order = shown.shown
  const setItem = useSetItem()
  const setItemError = useSetItemError()
  const list = order?.shopping_list ?? null
  const item = list?.items.find((candidate) => candidate.id === itemId)
  // It applies only to this session's draft, with its list shopped and the item on it (or while
  // it loads); otherwise the flow under it shows why (someone else has it, it's gone or placed,
  // the list is being made again).
  const holder = order ? lockHolder(order.lock, session, userId) : null
  const gone = shown.error instanceof ApiError && shown.error.status === 404
  const applies =
    order === undefined
      ? shown.isPending
      : !gone &&
        order.status === 'draft' &&
        (holder === 'this' || holder === 'none' || (holder === 'you elsewhere' && acquiring)) &&
        listState(list) === 'ready' &&
        item !== undefined
  useEffect(() => {
    if (!applies) {
      logger.info('orders.product_left', {
        orderId, itemId, status: order?.status, holder, listState: listState(list),
        itemFound: item !== undefined, gone, pending: shown.isPending,
      })
      onLeave()
    }
  }, [applies, onLeave, orderId, itemId, order?.status, holder, list, item, gone, shown.isPending])

  if (!order || !item) return <NavPage title="Product">{null}</NavPage>

  const store = storeName(order)

  const names = new Map(pickedMeals(order).map(({ meal }) => [meal.id, meal.name]))
  const meals = mealNames(item, names)
  const mealsFor = meals && `For ${meals}`
  const { product } = item

  function change(edit: { removed?: boolean; count?: number; alternative?: string }) {
    haptics.tap()
    setItem.mutate({ kind: 'item', orderId, itemId, session, ...edit })
  }

  function useAlternative(alternative: string) {
    haptics.tap()
    setItem.mutate({ kind: 'item', orderId, itemId, session, alternative }, { onSuccess: onDone })
  }

  if (view === 'more-alternatives') {
    return (
      <NavPage title="More alternatives">
        {setItemError && <Notice>Couldn't save that: {setItemError.message}</Notice>}
        <ProductAlternatives
          title={item.name}
          products={item.more_alternatives.filter((alternative) =>
            alternative.product_id !== product?.product_id &&
            !item.alternatives.some((suggested) => suggested.product_id === alternative.product_id),
          )}
          query={item.more_alternatives_query}
          emptyReason="No other matching products found."
          onUse={useAlternative}
        />
      </NavPage>
    )
  }

  return (
    <NavPage title={product?.name ?? item.name}>
      {setItemError && <Notice>Couldn't save that: {setItemError.message}</Notice>}
      {/* Not buying it (or skipping it): as on its tile, crossed out and faded, in place, so nothing moves. */}
      <header className={`${styles.hero} ${item.removed ? styles.out : ''}`}>
        {product && <ProductPicture product={product} size="hero" />}
        <h1 className={styles.item}>{item.name}</h1>
        <p className={styles.product}>
          {product ? (
            <>
              {product.bought_before && <BoughtBefore />}
              {product.name}
            </>
          ) : (
            `${store} doesn't have it`
          )}
        </p>
      </header>

      {product ? (
        <>
          <Group>
            <Row
              label="Buy"
              detail={<Switch checked={!item.removed} label={`Buy ${item.name}`} onChange={(on) => change({ removed: !on })} />}
            />
            <Row
              label="How many"
              detail={
                <Stepper
                  inRow
                  value={product.count}
                  min={1}
                  max={MAX_COUNT}
                  label={`How many ${product.name}`}
                  disabled={item.removed}
                  onChange={(count) => change({ count })}
                />
              }
            />
          </Group>
          <Group title="Details" footer={mealsFor || undefined}>
            <Row label="Price" detail={priceDetail(product)} />
            {productSize(product) && <Row label="Size" detail={productSize(product)} />}
            <Row label="Bought before" detail={product.bought_before ? 'Yes' : 'No'} />
            {item.quantity !== null && <Row label="Recipes need" detail={itemAmount(item)} />}
          </Group>
        </>
      ) : (
        <>
          <Group>
            <Row
              label="Skip"
              detail={<Switch checked={item.removed} label={`Skip ${item.name}`} onChange={(on) => change({ removed: on })} />}
            />
          </Group>
          <Group title="Details" footer={[mealsFor, item.skippable_reason].filter(Boolean).join('. ') || undefined}>
            <Row label="Recipes need" detail={item.quantity === null ? 'Some' : itemAmount(item)} />
          </Group>
        </>
      )}

      <ProductAlternatives
        title="Recommended alternatives"
        products={item.alternatives.filter((alternative) => alternative.product_id !== product?.product_id)}
        emptyReason={item.no_alternatives_reason}
        browse={item.more_alternatives_query === null || !onMoreAlternatives ? undefined : {
          label: 'More alternatives',
          onClick: () => {
            haptics.tap()
            onMoreAlternatives()
          },
        }}
        // Back to the list once it's saved; a refusal stays here, said above.
        onUse={useAlternative}
      />
    </NavPage>
  )
}

/** "$8.07 ($2.69 each)", "$3.18 (estimate)", or "No price". */
function priceDetail(product: Product): string {
  const cost = productCost(product)
  if (cost === null) return 'No price'
  if (isWeighed(product)) return `${cost} (estimate)`
  if (product.count > 1 && product.price_cents !== null) return `${cost} (${formatMoney(product.price_cents)} each)`
  return cost
}
