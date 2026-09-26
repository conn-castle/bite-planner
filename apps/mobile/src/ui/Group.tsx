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
}

/** A row of a group: a label (and a line under it), then a value. */
export function Row({ label, sub, detail, onClick, disabled, tone = 'default', chevron }: RowProps) {
  const content = (
    <>
      <span className={styles.label}>
        <span className={styles.text}>{label}</span>
        {sub != null && <span className={styles.sub}>{sub}</span>}
      </span>
      {detail != null && <span className={styles.detail}>{detail}</span>}
      {chevron && <Icon name="chevR" className={styles.chevron} />}
    </>
  )
  const className = `${styles.row} ${tone === 'danger' ? styles.danger : ''}`
  return onClick ? (
    <button type="button" className={`${className} ${styles.pressable}`} onClick={onClick} disabled={disabled}>
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
}

/** A row that opens something, with a picture: e.g. a meal in a list of meals. */
export function ListRow({ art, overline, label, mark, onClick, onPressIntent }: ListRowProps) {
  const press = usePressIntent(() => onPressIntent?.())
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
