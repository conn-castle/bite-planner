import type { ReactNode } from 'react'
import { Icon } from './Icon'
import styles from './OrderListCard.module.css'
import { orderColors } from './orderColor'

interface OrderListCardProps {
  /** The order's number, which gives its colors. */
  number: number
  /** The small line on top, in the order's color, e.g. "Order 3 · Sep 20". */
  overline: string
  title: string
  /** At the end of the header, e.g. a status pill. */
  end?: ReactNode
  /** Makes the header open the order (with a chevron). */
  onOpen?: () => void
  /** Its rows, e.g. the order's meals. */
  children: ReactNode
}

/**
 * An order's meals as a card with a header in the order's color
 * (spec/mockup.html: .hcard), e.g. in History.
 */
export function OrderListCard({ number, overline, title, end, onOpen, children }: OrderListCardProps) {
  const header = (
    <>
      <span className={styles.dot} aria-hidden="true" />
      <span className={styles.text}>
        <span className={styles.overline}>{overline}</span>
        <span className={styles.title}>{title}</span>
      </span>
      {end}
      {onOpen && <Icon name="chevR" className={styles.chevron} />}
    </>
  )
  return (
    <section className={styles.card} style={orderColors(number)}>
      {onOpen ? (
        <button type="button" className={`${styles.header} ${styles.pressable}`} onClick={onOpen}>
          {header}
        </button>
      ) : (
        <div className={styles.header}>{header}</div>
      )}
      {children}
    </section>
  )
}
