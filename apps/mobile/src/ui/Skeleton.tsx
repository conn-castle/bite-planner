import styles from './Skeleton.module.css'

/** A shimmering placeholder block while content loads. */
export function Skeleton({ height, width = '100%', radius }: { height: number; width?: number | string; radius?: number }) {
  return (
    <div
      className={styles.skeleton}
      style={{ height, width, borderRadius: radius }}
      aria-hidden="true"
    />
  )
}
