import type { ReactNode } from 'react'
import styles from './AgentWork.module.css'
import { ProgressBar } from './ProgressBar'

interface AgentWorkProps {
  /** The picture in the orb. */
  art: ReactNode
  title: ReactNode
  /** While it works: how long it has taken ("1:05"), or "Starting". */
  took: string | null
  /** While it works: what to expect. */
  lede?: ReactNode
  /** Under it: what to do meanwhile, or why it stopped. */
  children?: ReactNode
}

/**
 * The agent at work on something the flow waits for: an orb (turning while it
 * works), a title, and while it works, the progress bar and how long it has
 * taken (spec/mockup.html: .wait, .orb, .gbar).
 */
export function AgentWork({ art, title, took, lede, children }: AgentWorkProps) {
  const working = took !== null
  return (
    <div className={styles.wait}>
      <div className={`${styles.orb} ${working ? styles.spinning : ''}`} aria-hidden="true">
        {art}
      </div>
      <h2 className={styles.title}>{title}</h2>
      {working && (
        <>
          {lede && <p className={styles.lede}>{lede}</p>}
          <ProgressBar className={styles.progress} />
          <p className={styles.elapsed} role="timer">
            {took}
          </p>
        </>
      )}
      {children}
    </div>
  )
}
