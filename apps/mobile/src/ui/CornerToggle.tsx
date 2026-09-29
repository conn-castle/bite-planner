import type { IconName } from './Icon'
import { Icon } from './Icon'
import styles from './CornerToggle.module.css'

interface CornerToggleProps {
  /** Whether its card's thing is off (not bought, not shown again). */
  off: boolean
  /** Read out for the button: what tapping it does now (so no pressed state too). */
  label: string
  onToggle: () => void
  /** Its icon while off, to put it back (e.g. ↺). */
  offIcon: IconName
}

/**
 * The red × over a card's top-right corner, a little off it: tap it to take the
 * card's thing off, and again to put it back (a product's tile, a meal to pick).
 * Its card is `position: relative` and doesn't clip it.
 */
export function CornerToggle({ off, label, onToggle, offIcon }: CornerToggleProps) {
  return (
    <button
      type="button"
      className={`${styles.toggle} ${off ? styles.undo : ''} cc-hit`}
      aria-label={label}
      onClick={onToggle}
    >
      <Icon name={off ? offIcon : 'x'} />
    </button>
  )
}
