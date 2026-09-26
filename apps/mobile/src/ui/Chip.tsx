import type { ReactNode } from 'react'
import styles from './Chip.module.css'
import { Icon, type IconName } from './Icon'

interface ChipProps {
  /** Outlined (the default), accent for something ready to act on, success, or danger (e.g. overdue). */
  tone?: 'default' | 'accent' | 'success' | 'danger'
  icon?: IconName
  children: ReactNode
}

/** A small status pill, e.g. "Draft" or "Ready" on an order card. */
export function Chip({ tone = 'default', icon, children }: ChipProps) {
  return (
    <span className={`${styles.chip} ${styles[tone]}`}>
      {icon && <Icon name={icon} />}
      {children}
    </span>
  )
}

/** A small flat label, without the chip's ring (spec/mockup.html: .pill, .pill.ok), e.g. "Order placed" in History. */
export function Pill({ tone = 'default', children }: { tone?: 'default' | 'success'; children: ReactNode }) {
  return <span className={`${styles.pill} ${tone === 'success' ? styles.pillSuccess : ''}`}>{children}</span>
}
