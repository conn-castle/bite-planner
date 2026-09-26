import type { ReactNode } from 'react'
import { Icon, type IconName } from './Icon'
import styles from './OrderCard.module.css'
import { orderColors } from './orderColor'

/**
 * How an order's card looks: placed (white, its color behind the head); or,
 * for the next order, not due yet (dashed), its reminders started (white),
 * ideas being made (white), ready to act on (accent), due today or tomorrow
 * (accent, raised), overdue (red), or another member placing it (their color).
 */
export type OrderCardLook = 'placed' | 'draft' | 'remind' | 'working' | 'ready' | 'soon' | 'late' | 'locked'

interface OrderCardProps {
  number: number
  look: OrderCardLook
  chip: ReactNode
  title: ReactNode
  subtitle: ReactNode
  /** Beside the title, e.g. the days left to decide. */
  aside?: ReactNode
  /** Makes the head a button, e.g. to open the order. */
  onOpen?: () => void
  /** Under the head: a track, bodies, dividers, a fold of meals. */
  children?: ReactNode
  /** In a page of its own, e.g. the order's view, rather than in the list on Home. */
  className?: string
}

/** An order as a card, in its order's color. */
export function OrderCard({ number, look, chip, title, subtitle, aside, onOpen, children, className }: OrderCardProps) {
  const head = (
    <>
      <span className={styles.over}>
        <span className={styles.order}>
          <i className={styles.mark} aria-hidden="true" />
          Order {number}
        </span>
        {chip}
      </span>
      <span className={styles.titleRow}>
        <span className={styles.title} role="heading" aria-level={2}>
          {title}
        </span>
        {aside}
      </span>
      <span className={styles.subtitle}>{subtitle}</span>
    </>
  )
  return (
    <article
      className={[styles.card, styles[look], className].filter(Boolean).join(' ')}
      style={orderColors(number)}
    >
      {onOpen ? (
        <button type="button" className={`${styles.head} ${styles.pressable}`} onClick={onOpen}>
          {head}
        </button>
      ) : (
        <div className={styles.head}>{head}</div>
      )}
      {children}
    </article>
  )
}

/** A section of a card's content, padded like its head. */
export function OrderCardBody({ children }: { children: ReactNode }) {
  return <div className={styles.body}>{children}</div>
}

/** A card's main button, and any under it, after its lines. */
export function OrderCardActions({ children }: { children: ReactNode }) {
  return <div className={styles.actions}>{children}</div>
}

/** A hairline between a card's sections. */
export function OrderCardDivider() {
  return <div className={styles.divider} />
}

/** A line of detail on an order card, after an icon; red for a warning. */
export function OrderCardLine({
  icon,
  tone = 'default',
  children,
}: {
  icon: IconName
  tone?: 'default' | 'danger'
  children: ReactNode
}) {
  return (
    <p className={`${styles.line} ${tone === 'danger' ? styles.danger : ''}`}>
      <Icon name={icon} />
      <span>{children}</span>
    </p>
  )
}

/** A note on a placed order's card, after an icon: e.g. when its groceries arrive. */
export function OrderCardNote({ icon, children }: { icon: IconName; children: ReactNode }) {
  return (
    <p className={styles.note}>
      <Icon name={icon} />
      <span>{children}</span>
    </p>
  )
}
