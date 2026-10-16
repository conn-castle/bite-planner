import type { ReactNode } from 'react'
import { Icon, type IconName } from './Icon'
import styles from './Stats.module.css'

export interface Stat {
  value: string | number | null
  label: string
  icon?: IconName
  /** Visible suffix for a recorded value; omitted when the value is missing. */
  unit?: string
  /** Accessible meaning of a missing meal value when its caption alone needs context. */
  missingLabel?: string
}

interface StatsProps {
  stats: readonly Stat[]
  /** Summary totals, recipe facts, or the larger icon/value boxes inside a Pick card's button. */
  variant?: 'summary' | 'recipe' | 'meal'
  className?: string
}

/** One row of value/label boxes (spec/mockup.html: .lsum and recipe .facts). */
export function Stats({
  stats,
  variant = 'summary',
  className = '',
}: StatsProps) {
  // Meal cards need phrasing content inside their recipe-opening button.
  const Group: 'span' | 'dl' | 'div' = variant === 'meal' ? 'span' : variant === 'recipe' ? 'dl' : 'div'
  const Cell: 'span' | 'div' = variant === 'meal' ? 'span' : 'div'
  const Value: 'dd' | 'b' = variant === 'recipe' ? 'dd' : 'b'
  const Label: 'dt' | 'small' = variant === 'recipe' ? 'dt' : 'small'
  const variantClass: string = variant === 'summary' ? '' : styles[variant]
  return (
    <Group className={`${styles.stats} ${variantClass} ${className}`}>
      {stats.map(({ value, label, icon, unit, missingLabel }: Stat) => {
        const missingMealValue: boolean = variant === 'meal' && value === null
        const valueElement: ReactNode = (
          <Value className={styles.value}>
            {icon && <Icon name={icon} />}
            <span
              className={`${styles.number} ${value === null ? styles.unavailable : ''}`}
              role={missingMealValue ? 'img' : undefined}
              aria-label={missingMealValue ? (missingLabel ?? `${label} not listed`) : undefined}
            >
              {missingMealValue ? '—' : value === null ? 'Unavailable' : value}
              {value !== null && unit && <> <span className={styles.unit}>{unit}</span></>}
            </span>
          </Value>
        )
        const labelElement: ReactNode = <Label className={styles.label}>{label}</Label>
        return (
          <Cell key={label} className={styles.cell}>
            {variant === 'recipe' ? labelElement : valueElement}
            {variant === 'recipe' ? valueElement : labelElement}
          </Cell>
        )
      })}
    </Group>
  )
}
