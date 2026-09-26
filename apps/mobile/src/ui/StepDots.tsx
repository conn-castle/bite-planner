import type { ReactNode } from 'react'
import styles from './StepDots.module.css'

interface StepDotsProps {
  /** The steps' names, in order. */
  steps: readonly string[]
  /** The step reached (0-based): it and those before it are filled. */
  at: number
  /** The flow's own progress (ink), or another member's (smaller, in their color). */
  look?: 'flow' | 'other'
  /** Beside the dots, e.g. "Started 6 min ago · Pick meals". */
  label?: ReactNode
}

/**
 * Progress through a flow's steps, as a row of short bars, with a label beside
 * them if given (spec/mockup.html: .dots, .pstep).
 */
export function StepDots({ steps, at, look = 'flow', label }: StepDotsProps) {
  const dots = (
    <div
      className={`${styles.dots} ${look === 'other' ? styles.other : ''}`}
      role="img"
      aria-label={`Step ${at + 1} of ${steps.length}: ${steps[at]}`}
    >
      {steps.map((name, index) => (
        <i key={name} className={index <= at ? styles.on : undefined} />
      ))}
    </div>
  )
  if (label === undefined) return dots
  return (
    <div className={styles.progress}>
      {dots}
      <span>{label}</span>
    </div>
  )
}
