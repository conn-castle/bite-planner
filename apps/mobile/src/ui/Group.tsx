import type { ReactNode } from 'react'
import { usePressIntent } from '../lib/usePressIntent'
import styles from './Group.module.css'
import { Icon, type IconName } from './Icon'

interface GroupProps {
  title?: string
  /** A note under the card, after an icon if given. */
  footer?: ReactNode
  footerIcon?: IconName
  children: ReactNode
}

/** A titled card of related rows. */
export function Group({ title, footer, footerIcon, children }: GroupProps) {
  return (
    <section>
      {title && <h2 className={styles.title}>{title}</h2>}
      <div className={styles.card}>{children}</div>
      {footer && (
        <p className={styles.footer}>
          {footerIcon && <Icon name={footerIcon} />}
          <span>{footer}</span>
        </p>
      )}
    </section>
  )
}

interface RowProps {
  label: ReactNode
  /** A quieter line under the label. */
  sub?: ReactNode
  /** A value at the end of the row. */
  detail?: ReactNode
  /** Makes the row a button. */
  onClick?: () => void
  disabled?: boolean
  tone?: 'default' | 'danger'
  /** Shows a chevron: the row opens something. */
  chevron?: boolean
  /** The row opens and closes what's under it (the chevron turns down while open). */
  expanded?: boolean
}

/** A row of a group: a label (and a line under it), then a value. */
export function Row({ label, sub, detail, onClick, disabled, tone = 'default', chevron, expanded }: RowProps) {
  const content = (
    <>
      <span className={styles.label}>
        <span className={styles.text}>{label}</span>
        {sub != null && <span className={styles.sub}>{sub}</span>}
      </span>
      {detail != null && (
        // A row that opens in place shows its value as the choice it is: strong, and accented while open.
        <span className={`${styles.detail} ${expanded !== undefined ? styles.choice : ''} ${expanded ? styles.open : ''}`}>
          {detail}
        </span>
      )}
      {(chevron || expanded !== undefined) && (
        <Icon name="chevR" className={`${styles.chevron} ${expanded ? styles.open : ''}`} />
      )}
    </>
  )
  const className = `${styles.row} ${tone === 'danger' ? styles.danger : ''}`
  return onClick ? (
    <button
      type="button"
      className={`${className} ${styles.pressable}`}
      onClick={onClick}
      disabled={disabled}
      aria-expanded={expanded}
    >
      {content}
    </button>
  ) : (
    <div className={className}>{content}</div>
  )
}

interface ListRowProps {
  /** A 40px picture at the start, e.g. the meal's. */
  art: ReactNode
  /** A small line over the name, e.g. "35 min". */
  overline?: ReactNode
  label: ReactNode
  /** A small mark before the chevron, e.g. the meal's rating. */
  mark?: ReactNode
  onClick: () => void
  /** Once a press is held briefly, before the tap ends: e.g. to start loading what it opens. */
  onPressIntent?: () => void
  /** A control at the end in place of the chevron, e.g. a stepper; the rest of the row still opens. */
  control?: ReactNode
}

/** A row that opens something, with a picture: e.g. a meal in a list of meals. */
export function ListRow({ art, overline, label, mark, onClick, onPressIntent, control }: ListRowProps) {
  const press = usePressIntent(() => onPressIntent?.())
  if (control) {
    // A button can't hold another control, so the part that opens is its own button.
    return (
      <div className={`${styles.listRow} ${styles.withControl}`}>
        <button type="button" className={`${styles.opens} ${styles.pressable}`} onClick={onClick} {...press}>
          <span className={styles.art}>{art}</span>
          <span className={styles.label}>
            {overline != null && <span className={styles.overline}>{overline}</span>}
            <span className={styles.name}>{label}</span>
          </span>
          {mark}
        </button>
        {control}
      </div>
    )
  }
  return (
    <button type="button" className={`${styles.listRow} ${styles.pressable}`} onClick={onClick} {...press}>
      <span className={styles.art}>{art}</span>
      <span className={styles.label}>
        {overline != null && <span className={styles.overline}>{overline}</span>}
        <span className={styles.name}>{label}</span>
      </span>
      {mark}
      <Icon name="chevR" className={styles.chevron} />
    </button>
  )
}
