import { Icon } from './Icon'
import styles from './AddMealButton.module.css'

/** One selection control shared by meal cards and recipe toolbars. */
export function AddMealButton({ name, added, disabled = false, onToggle, className = '', compact = false }: {
  name: string; added: boolean; disabled?: boolean; onToggle: () => void; className?: string; compact?: boolean
}) {
  return <button type="button" className={`cc-hit ${styles.button} ${compact ? styles.compact : ''} ${className}`} onClick={onToggle}
    disabled={disabled} aria-pressed={added} aria-label={`${added ? 'Remove' : 'Add'} ${name}`}>
    <Icon name={added ? 'check' : 'plus'} />{added ? 'Added' : 'Add'}
  </button>
}
