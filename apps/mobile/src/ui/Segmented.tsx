import styles from './Segmented.module.css'

interface SegmentedProps<T extends string> {
  options: readonly { value: T; label: string }[]
  value: T
  onChange: (value: T) => void
  /** What the choice is, for screen readers, e.g. "Show". */
  label: string
  className?: string
}

/** A segmented switch between views (spec/mockup.html: .seg-ctl), e.g. History's "By order" and "All meals". */
export function Segmented<T extends string>({ options, value, onChange, label, className }: SegmentedProps<T>) {
  return (
    <div className={`${styles.segmented} ${className ?? ''}`} role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className={`${styles.segment} ${option.value === value ? styles.on : ''}`}
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}
