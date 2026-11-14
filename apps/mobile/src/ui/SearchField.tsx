import { Icon } from './Icon'
import styles from './SearchField.module.css'

interface SearchFieldProps {
  value: string
  onChange: (value: string) => void
  placeholder: string
  className?: string
}

/** A search box (not in the mockup): a soft field with a search icon, and a clear button once there's text. */
export function SearchField({ value, onChange, placeholder, className }: SearchFieldProps) {
  return (
    <div className={`${styles.field} ${className ?? ''}`}>
      <Icon name="search" className={styles.icon} />
      <input
        className={styles.input}
        type="search"
        enterKeyHint="search"
        autoComplete="off"
        autoCorrect="off"
        value={value}
        placeholder={placeholder}
        aria-label={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
      {value && (
        <button type="button" className={`${styles.clear} bp-hit`} aria-label="Clear search" onClick={() => onChange('')}>
          <Icon name="x" />
        </button>
      )}
    </div>
  )
}
