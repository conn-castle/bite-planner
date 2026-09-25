import type { ReactNode } from 'react'
import styles from './Chip.module.css'
import { Icon, type IconName } from './Icon'

interface ChipProps {
  /** Outlined (the default), accent for something ready to act on, or success. */
  tone?: 'default' | 'accent' | 'success'
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
