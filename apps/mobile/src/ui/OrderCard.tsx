import type { CSSProperties, ReactNode } from 'react'
import { Icon, type IconName } from './Icon'
import styles from './OrderCard.module.css'
import { orderColor } from './orderColor'

type OrderColors = CSSProperties & { '--c': string; '--soft': string; '--deep': string }

interface OrderCardProps {
  number: number
  /**
   * A draft not started (dashed), a draft whose ideas are being made (white),
   * or a draft ready to act on (accent).
   */
  look: 'draft' | 'working' | 'ready'
  chip: ReactNode
  title: ReactNode
  subtitle: ReactNode
  /** The card's body, under its head: lines, then its actions. */
  children: ReactNode
}

/** An order as a card on the home screen, in its order's color. */
export function OrderCard({ number, look, chip, title, subtitle, children }: OrderCardProps) {
  const colors: OrderColors = {
    '--c': orderColor(number),
    '--soft': orderColor(number, 'soft'),
    '--deep': orderColor(number, 'deep'),
  }
  return (
    // A working draft is the plain card.
    <article className={look === 'working' ? styles.card : `${styles.card} ${styles[look]}`} style={colors}>
      <div className={styles.head}>
        <div className={styles.over}>
          <span className={styles.order}>
            <i className={styles.mark} aria-hidden="true" />
            Order {number}
          </span>
          {chip}
        </div>
        <h2 className={styles.title}>{title}</h2>
        <p className={styles.subtitle}>{subtitle}</p>
      </div>
      <div className={styles.body}>{children}</div>
    </article>
  )
}

/** A line of detail on an order card, after an icon. */
export function OrderCardLine({ icon, children }: { icon: IconName; children: ReactNode }) {
  return (
    <p className={styles.line}>
      <Icon name={icon} />
      <span>{children}</span>
    </p>
  )
}
