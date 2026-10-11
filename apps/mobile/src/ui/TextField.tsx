import type { InputHTMLAttributes, ReactElement } from 'react'
import styles from './TextField.module.css'

interface TextFieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string
}

/** A labelled text input, shared by sign-in and settings forms. */
export function TextField({ label, className, ...props }: TextFieldProps): ReactElement {
  return <label className={styles.label}>{label}<input className={[styles.field, className].filter(Boolean).join(' ')} {...props} /></label>
}
