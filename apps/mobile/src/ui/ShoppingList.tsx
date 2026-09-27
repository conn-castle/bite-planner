import { type ReactNode, useState } from 'react'
import type { Aisle, ShoppingItem } from '../lib/api/types'
import { Icon } from './Icon'
import styles from './ShoppingList.module.css'

const AISLES: { aisle: Aisle; name: string }[] = [
  { aisle: 'produce', name: 'Produce' },
  { aisle: 'meat_seafood', name: 'Meat & seafood' },
  { aisle: 'dairy_eggs', name: 'Dairy & eggs' },
  { aisle: 'frozen', name: 'Frozen' },
  { aisle: 'pantry', name: 'Pantry' },
]

interface ShoppingListProps {
  /** The items to buy (not the ones assumed on hand), by aisle. */
  items: ShoppingItem[]
  /** "2 lb", "8": how much of an item, as shown. */
  amount: (item: ShoppingItem) => string
  /** "for Fajitas, Curry". */
  forMeals: (item: ShoppingItem) => string
  /** Makes the list editable: remove or restore an item, and change its amount with this control. */
  edit?: {
    onRemove: (item: ShoppingItem, removed: boolean) => void
    amountControl: (item: ShoppingItem) => ReactNode
  }
}

/**
 * A shopping list by aisle: each item's amount, name, and meals; editable, a
 * button to take it off (or put it back), and its amount opens a control to
 * change it (spec/mockup.html: .aisle, .li-row).
 */
export function ShoppingList({ items, amount, forMeals, edit }: ShoppingListProps) {
  const [open, setOpen] = useState<string | null>(null)
  return (
    <>
      {AISLES.map(({ aisle, name }) => {
        const rows = items.filter((item) => item.aisle === aisle)
        if (rows.length === 0) return null
        return (
          <section key={aisle}>
            <h3 className={styles.aisle}>
              <span>{name}</span>
              <span>{rows.filter((item) => !item.removed).length}</span>
            </h3>
            {rows.map((item) => {
              const changing = edit && open === item.id && !item.removed && item.quantity !== null
              return (
                <div key={item.id} className={styles.item}>
                  <div className={`${styles.row} ${item.removed ? styles.out : ''}`}>
                    {edit && !item.removed && item.quantity !== null ? (
                      <button
                        type="button"
                        className={`${styles.amount} ${styles.change}`}
                        aria-expanded={changing}
                        aria-label={`Change how much ${item.name}: ${amount(item)}`}
                        onClick={() => setOpen(changing ? null : item.id)}
                      >
                        {amount(item)}
                      </button>
                    ) : (
                      <span className={styles.amount}>{amount(item)}</span>
                    )}
                    <span className={styles.name}>
                      <b>{item.name}</b>
                      <small>
                        {item.removed && <span className={styles.removed}>Removed · </span>}
                        {forMeals(item)}
                      </small>
                    </span>
                    {edit &&
                      (item.removed ? (
                        <button type="button" className={`${styles.restore} cc-hit`} onClick={() => edit.onRemove(item, false)}>
                          <Icon name="undo" />
                          Restore
                        </button>
                      ) : (
                        <button
                          type="button"
                          className={`${styles.remove} cc-hit`}
                          aria-label={`Remove ${item.name}`}
                          onClick={() => edit.onRemove(item, true)}
                        >
                          <Icon name="minus" />
                        </button>
                      ))}
                  </div>
                  {changing && <div className={styles.control}>{edit.amountControl(item)}</div>}
                </div>
              )
            })}
          </section>
        )
      })}
    </>
  )
}
