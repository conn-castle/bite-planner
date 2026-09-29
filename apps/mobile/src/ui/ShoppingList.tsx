import type { CSSProperties, ReactNode } from 'react'
import type { Aisle, Product, ShoppingItem } from '../lib/api/types'
import { formatMoney, isWeighed, productCost } from '../lib/products'
import { CornerToggle } from './CornerToggle'
import { Icon } from './Icon'
import { Stepper } from './Stepper'
import styles from './ShoppingList.module.css'

/** The aisle colors defined in theme/tokens.css (--cc-aisle-produce-soft …, with -deep). */
type AisleColor = 'produce' | 'meat' | 'dairy' | 'frozen' | 'pantry' | 'other'

type AisleColors = CSSProperties & { '--aisle-soft': string; '--aisle-deep': string }

/** An aisle's colors as CSS variables, for its card's head: --aisle-soft, --aisle-deep. */
function aisleColors(color: AisleColor): AisleColors {
  return {
    '--aisle-soft': `var(--cc-aisle-${color}-soft)`,
    '--aisle-deep': `var(--cc-aisle-${color}-deep)`,
  }
}

/** The card for items the store doesn't have: an aisle card in the colors of a problem. */
const MISSING_COLORS: AisleColors = { '--aisle-soft': 'var(--cc-danger-soft)', '--aisle-deep': 'var(--cc-danger)' }

/** The aisles in the store's order, each with its card head's colors. */
const AISLES: { aisle: Aisle; name: string; color: AisleColor }[] = [
  { aisle: 'produce', name: 'Produce', color: 'produce' },
  { aisle: 'meat_seafood', name: 'Meat & seafood', color: 'meat' },
  { aisle: 'dairy_eggs', name: 'Dairy & eggs', color: 'dairy' },
  { aisle: 'frozen', name: 'Frozen', color: 'frozen' },
  { aisle: 'pantry', name: 'Pantry', color: 'pantry' },
  { aisle: 'other', name: 'Other', color: 'other' },
]

interface ShoppingListProps {
  /** The items to buy (not the ones assumed on hand), by aisle. */
  items: ShoppingItem[]
  /** "2 lb", "8": how much of an item, as shown. */
  amount: (item: ShoppingItem) => string
  /** "for Fajitas, Curry". */
  forMeals: (item: ShoppingItem) => string
  /**
   * Makes the list editable: each item's amount is a control (when it has one),
   * and a red × takes it off the list, or puts it back.
   */
  edit?: {
    onRemove: (item: ShoppingItem, removed: boolean) => void
    /** The item's amount control, greyed out while it's off the list. */
    amountControl: (item: ShoppingItem, disabled: boolean) => ReactNode
    /** Back to the amount worked out for the recipes (offered, under its name, once it was changed). */
    onReset: (item: ShoppingItem) => void
  }
  /** The list shopped at a store: each item is its product, in a tile. */
  shop?: {
    /** Tap a product to change it; without it, the products are read-only. */
    onOpen?: (item: ShoppingItem) => void
    /**
     * Each tile's stepper sets how many, and its red × takes the product off
     * the list, or puts it back; without it, the tile shows how many.
     */
    edit?: {
      onCount: (item: ShoppingItem, count: number) => void
      onRemove: (item: ShoppingItem, removed: boolean) => void
      /** The most of a product the stepper goes to. */
      maxCount: number
    }
    /**
     * The items the store doesn't have come first, in a red card, until the
     * member skips each or picks an alternative (Alternatives opens the item).
     */
    missing?: { store: string; onSkip: (item: ShoppingItem) => void }
  }
}

/**
 * A shopping list by aisle (spec/mockup.html: .aisle, .li-row). Each aisle is a
 * card whose head is a band in the aisle's color with its name and count (a review
 * decision over the mockup's small grey heading). Read-only, each item's amount
 * leads the row; editable, the row ends with the amount's control and the × (a
 * review decision over the mockup's − and Restore).
 */
export function ShoppingList({ items, amount, forMeals, edit, shop }: ShoppingListProps) {
  if (shop !== undefined) return <ShoppedList items={items} forMeals={forMeals} shop={shop} />
  return (
    <>
      {AISLES.map(({ aisle, name, color }) => {
        const rows = items.filter((item) => item.aisle === aisle)
        if (rows.length === 0) return null
        return (
          <section
            key={aisle}
            className={styles.card}
            style={aisleColors(color)}
          >
            <h3 className={styles.aisle}>
              <span>{name}</span>
              <span className={styles.count}>{rows.filter((item) => !item.removed).length}</span>
            </h3>
            {rows.map((item) => (
              <div key={item.id} className={`${styles.row} ${item.removed ? styles.out : ''}`}>
                {!edit && <span className={styles.amount}>{amount(item)}</span>}
                <span className={styles.name}>
                  <b>{item.name}</b>
                  {edit && !item.removed && item.quantity !== item.recipe_quantity ? (
                    // Changed: what the recipes need, and a way back to it (in place of its
                    // meals, so nothing moves). Reset keeps its room however narrow the row.
                    <small className={styles.changed}>
                      <span>Recipes need {amount({ ...item, quantity: item.recipe_quantity })} ·</span>
                      <button
                        type="button"
                        className={styles.reset}
                        aria-label={`Reset ${item.name} to what the recipes need`}
                        onClick={() => edit.onReset(item)}
                      >
                        Reset
                      </button>
                    </small>
                  ) : (
                    <small>
                      {item.removed && <span className={styles.removed}>Removed · </span>}
                      {forMeals(item)}
                    </small>
                  )}
                </span>
                {edit && (
                  <>
                    {item.quantity === null ? (
                      <span className={styles.fixedAmount}>{amount(item)}</span>
                    ) : (
                      edit.amountControl(item, item.removed)
                    )}
                    <button
                      type="button"
                      className={`${styles.remove} cc-hit`}
                      aria-pressed={item.removed}
                      aria-label={item.removed ? `Put ${item.name} back on the list` : `Remove ${item.name}`}
                      onClick={() => edit.onRemove(item, !item.removed)}
                    >
                      <Icon name="x" />
                    </button>
                  </>
                )}
              </div>
            ))}
          </section>
        )
      })}
    </>
  )
}

/** Still to decide: the store doesn't have it, and the member hasn't picked an alternative or skipped it. */
function isUndecided(item: ShoppingItem): boolean {
  return item.not_found && item.product === null && !item.removed
}

interface ShoppedListProps {
  items: ShoppingItem[]
  forMeals: (item: ShoppingItem) => string
  shop: NonNullable<ShoppingListProps['shop']>
}

/** The list shopped at a store: the items it doesn't have, then the products by aisle. */
function ShoppedList({ items, forMeals, shop }: ShoppedListProps) {
  const missing = shop.missing ? items.filter(isUndecided) : []
  const decide = shop.missing
  return (
    <>
      {decide && missing.length > 0 && (
        <section className={`${styles.card} ${styles.missing}`} style={MISSING_COLORS}>
          <h3 className={styles.aisle}>
            <span>Not at {decide.store}</span>
            <span className={styles.count}>{missing.length}</span>
          </h3>
          {missing.map((item) => (
            <div key={item.id} className={styles.row}>
              <span className={styles.name}>
                <b>{item.name}</b>
                <small>{forMeals(item)}</small>
              </span>
              <span className={styles.decide}>
                <button
                  type="button"
                  className={`${styles.pill} cc-hit`}
                  aria-label={`Skip ${item.name}`}
                  onClick={() => decide.onSkip(item)}
                >
                  Skip
                </button>
                <button
                  type="button"
                  className={`${styles.pill} ${styles.pillDark} cc-hit`}
                  aria-label={`Alternatives for ${item.name}`}
                  onClick={() => shop.onOpen?.(item)}
                >
                  Alternatives
                </button>
              </span>
            </div>
          ))}
        </section>
      )}
      {AISLES.map(({ aisle, name, color }) => {
        const rows = items.filter((item) => item.aisle === aisle && !missing.includes(item))
        if (rows.length === 0) return null
        return (
          <section key={aisle} className={styles.card} style={aisleColors(color)}>
            <h3 className={styles.aisle}>
              <span>{name}</span>
              <span className={styles.count}>{rows.filter((item) => !item.removed).length}</span>
            </h3>
            <div className={styles.tiles}>
              {rows.map((item) => (
                <ProductTile
                  key={item.id}
                  item={item}
                  store={decide?.store}
                  onOpen={shop.onOpen && (() => shop.onOpen?.(item))}
                  edit={shop.edit}
                />
              ))}
            </div>
          </section>
        )
      })}
    </>
  )
}

/** A product's picture, or a blank tile when the store has none. */
export function ProductPicture({ product, size }: { product: Product | null; size: 'hero' | 'tile' | 'small' }) {
  const className = `${styles.picture} ${PICTURE_SIZES[size]}`
  if (product?.image_url) return <img className={className} src={product.image_url} alt="" />
  return <span className={className} aria-hidden="true" />
}

const PICTURE_SIZES = { hero: styles.pictureHero, tile: styles.pictureTile, small: styles.pictureSmall }

/** The mark on products the household has bought before (spelled out in the product's popup). */
export function BoughtBefore() {
  return (
    <span className={styles.again} role="img" aria-label="Bought before">
      <Icon name="repeat" />
    </span>
  )
}

/** What the tile's count costs ("$8.07"), and the price of one when there's more than one ("$2.69 each"). */
function TilePrice({ product }: { product: Product }) {
  const cost = productCost(product)
  if (cost === null) return <span className={styles.tilePrice}>No price</span>
  return (
    <span className={styles.tilePrice}>
      {cost}
      {isWeighed(product) ? (
        <small>est.</small>
      ) : (
        product.count > 1 && product.price_cents !== null && <small>{formatMoney(product.price_cents)} each</small>
      )}
    </span>
  )
}

interface ProductTileProps {
  item: ShoppingItem
  /** The store, for an item it doesn't have (skipped). */
  store: string | undefined
  onOpen?: () => void
  edit?: NonNullable<ShoppingListProps['shop']>['edit']
}

/**
 * One item's product: its picture, the item (what the meals need), the product
 * under it (the bought-before mark first), and its price; then how many (a
 * stepper, when it can change). Tap it to open the product; the red × on its
 * corner takes it off the list, or puts it back.
 */
function ProductTile({ item, store, onOpen, edit }: ProductTileProps) {
  const { product } = item
  const content = (
    <>
      <ProductPicture product={product} size="tile" />
      <b className={styles.tileItem}>{item.name}</b>
      <span className={styles.tileProduct}>
        {product?.bought_before && <BoughtBefore />}
        {product?.name ?? `Not at ${store ?? 'the store'}`}
      </span>
      {item.removed ? (
        <span className={`${styles.tilePrice} ${styles.removed}`}>Not buying</span>
      ) : (
        product && <TilePrice product={product} />
      )}
    </>
  )
  return (
    <div className={`${styles.tile} ${item.removed ? styles.tileOut : ''}`}>
      {onOpen ? (
        <button
          type="button"
          className={styles.tileOpen}
          onClick={onOpen}
          aria-label={`${item.name}: ${product?.name ?? `not at ${store ?? 'the store'}`}`}
        >
          {content}
        </button>
      ) : (
        <div className={styles.tileOpen}>{content}</div>
      )}
      {product &&
        (edit ? (
          <div className={styles.tileStepper}>
            <Stepper
              wide
              value={product.count}
              min={1}
              max={edit.maxCount}
              label={`How many ${product.name}`}
              disabled={item.removed}
              onChange={(count) => edit.onCount(item, count)}
            />
          </div>
        ) : (
          <span className={styles.tileCount}>Qty {product.count}</span>
        ))}
      {edit && (
        <CornerToggle
          off={item.removed}
          offIcon="undo"
          label={item.removed ? `Buy ${item.name} after all` : `Don't buy ${item.name}`}
          onToggle={() => edit.onRemove(item, !item.removed)}
        />
      )}
    </div>
  )
}
