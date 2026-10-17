import type { InputHTMLAttributes, TextareaHTMLAttributes, ReactElement } from 'react'
import styles from './TextField.module.css'

type TextFieldProps = { label: string } & (
  | ({ multiline?: false } & InputHTMLAttributes<HTMLInputElement>)
  | ({ multiline: true } & TextareaHTMLAttributes<HTMLTextAreaElement>)
)

/** A labelled text input, shared by sign-in and settings forms. */
export function TextField(props: TextFieldProps): ReactElement {
  if (props.multiline) {
    const { label, className, multiline: _multiline, ...field } = props
    return <label className={styles.label}>{label}<textarea className={`${styles.field} ${styles.multiline} ${className ?? ''}`} {...field} /></label>
  }
  const { label, className, multiline: _multiline, ...field } = props
  return <label className={styles.label}>{label}<input className={[styles.field, className].filter(Boolean).join(' ')} {...field} /></label>
}
