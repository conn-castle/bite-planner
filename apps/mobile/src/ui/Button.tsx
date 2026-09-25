import type { ButtonHTMLAttributes } from 'react'
import styles from './Button.module.css'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'accent' | 'secondary' | 'plain' | 'danger'
  size?: 'large' | 'medium'
  /** Stretches across its container. */
  block?: boolean
}

/** The app's buttons: an ink pill for the main action (accent for committing, e.g. placing an order), a soft fill for the rest. */
export function Button({
  variant = 'primary',
  size = 'large',
  block = false,
  className,
  type = 'button',
  ...props
}: ButtonProps) {
  const classes = [styles.button, styles[variant], styles[size], block && styles.block, className]
  return <button type={type} className={classes.filter(Boolean).join(' ')} {...props} />
}

/** A round icon button for toolbars. */
export function IconButton({
  className,
  type = 'button',
  variant = 'secondary',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' }) {
  const classes = [styles.icon, styles[variant], className]
  return <button type={type} className={classes.filter(Boolean).join(' ')} {...props} />
}
