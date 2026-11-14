import { Icon } from './Icon'
import styles from './AddMealButton.module.css'

/** One selection control shared by meal cards and recipe toolbars. */
export function AddMealButton({ name, added, disabled = false, onToggle, className = '', compact = false, iconOnly = false }: {
  name: string; added: boolean; disabled?: boolean; onToggle: () => void; className?: string; compact?: boolean; iconOnly?: boolean
}) {
  return <button type="button" className={`bp-hit ${styles.button} ${compact ? styles.compact : ''} ${iconOnly ? styles.iconOnly : ''} ${className}`} onClick={onToggle}
    disabled={disabled} aria-pressed={added} aria-label={`${added ? 'Remove' : 'Add'} ${name}`}>
    <Icon name={added ? 'check' : 'plus'} />{!iconOnly && (added ? 'Added' : 'Add')}
  </button>
}
