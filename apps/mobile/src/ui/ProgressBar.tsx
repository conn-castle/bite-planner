import styles from './ProgressBar.module.css'

/**
 * Work under way with no known end (e.g. the agent making meal ideas): a thin
 * bar with a sweep running across it. Its width and margins come from where
 * it sits.
 */
export function ProgressBar({ className }: { className?: string }) {
  return <span className={className ? `${styles.bar} ${className}` : styles.bar} aria-hidden="true" />
}
