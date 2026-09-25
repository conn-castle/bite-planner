import type { ButtonHTMLAttributes } from 'react'
import styles from './Button.module.css'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'accent' | 'danger' | 'secondary' | 'white' | 'plain'
  /** Stretches across its container, as a rounded rectangle; always the large size. */
  block?: boolean
  /** The large size, e.g. a sheet footer's main action. */
  large?: boolean
}

/**
 * The app's buttons: an ink pill for the main action (accent for committing,
 * e.g. placing an order; danger when it's overdue), a soft fill for the rest,
 * or white on a tinted card.
 */
export function Button({
  variant = 'primary',
  block = false,
  large = false,
  className,
  type = 'button',
  ...props
}: ButtonProps) {
  const classes = [styles.button, styles[variant], (block || large) && styles.large, block && styles.block, 'cc-hit', className]
  return <button type={type} className={classes.filter(Boolean).join(' ')} {...props} />
}

/**
 * A round icon button: a soft fill (e.g. a sheet's Close, or Back), or white
 * on the warm page (e.g. New order beside a title). It takes taps across at
 * least 44×44, whatever its drawn size.
 */
export function IconButton({
  className,
  type = 'button',
  variant,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant: 'fill' | 'white' }) {
  const classes = [styles.icon, styles[variant], 'cc-hit', className]
  return <button type={type} className={classes.filter(Boolean).join(' ')} {...props} />
}
