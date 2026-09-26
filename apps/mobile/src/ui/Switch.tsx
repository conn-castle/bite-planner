import { IonToggle } from '@ionic/react'
import styles from './Switch.module.css'

interface SwitchProps {
  checked: boolean
  /** Read out for the switch (the row's label says it on screen). */
  label: string
  onChange: (checked: boolean) => void
  disabled?: boolean
}

/** An on/off switch at the end of a settings row: iOS's own, in the app's accent. */
export function Switch({ checked, label, onChange, disabled = false }: SwitchProps) {
  return (
    <IonToggle
      className={styles.switch}
      checked={checked}
      disabled={disabled}
      aria-label={label}
      onIonChange={(event) => onChange(event.detail.checked)}
    />
  )
}
