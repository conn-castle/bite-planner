import styles from './ChoiceChips.module.css'

interface ChoiceChipsProps<T extends string> {
  options: readonly { value: T; label: string }[]
  value: T
  onChange: (value: T) => void
  /** What's chosen, for screen readers, e.g. "Filter". */
  label: string
  className?: string
}

/** A row of chips to choose one from, scrolling sideways when they don't fit (not in the mockup), e.g. History's filters. */
export function ChoiceChips<T extends string>({ options, value, onChange, label, className }: ChoiceChipsProps<T>) {
  return (
    <div className={`${styles.chips} ${className ?? ''}`} role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className={`${styles.chip} ${option.value === value ? styles.on : ''}`}
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}
