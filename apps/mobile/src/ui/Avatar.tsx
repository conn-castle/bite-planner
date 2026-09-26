import styles from './Avatar.module.css'

interface AvatarProps {
  /** Their name; the circle shows its first letter. */
  name: string | null
  /**
   * xs inside a chip, sm beside a title, md on a card, lg on a page of its own.
   * md and lg have a turning ring: they're at work (e.g. placing an order).
   */
  size: 'xs' | 'sm' | 'md' | 'lg'
}

/** Another member, as the first letter of their name in a circle (spec/mockup.html: .who). */
export function Avatar({ name, size }: AvatarProps) {
  const initial = name?.trim().charAt(0).toUpperCase() || '?'
  return (
    <span className={`${styles.avatar} ${styles[size]}`} aria-hidden="true">
      {initial}
    </span>
  )
}
