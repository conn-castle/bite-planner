import type { CSSProperties, ReactNode } from 'react'
import type { Aisle, ShoppingItem } from '../lib/api/types'
import { Icon } from './Icon'
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
}

/**
 * A shopping list by aisle (spec/mockup.html: .aisle, .li-row). Each aisle is a
 * card whose head is a band in the aisle's color with its name and count (a review
 * decision over the mockup's small grey heading). Read-only, each item's amount
 * leads the row; editable, the row ends with the amount's control and the × (a
 * review decision over the mockup's − and Restore).
 */
export function ShoppingList({ items, amount, forMeals, edit }: ShoppingListProps) {
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
