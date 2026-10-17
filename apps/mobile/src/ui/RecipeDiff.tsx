import type { ReactNode } from 'react'
import styles from './RecipeDiff.module.css'

export interface DiffLine { kind: 'same' | 'removed' | 'added'; text: string }
export interface DiffSection { title: string; lines: DiffLine[] }

/** Full recipe comparison, shared line treatment for facts, ingredients and steps. */
export function RecipeDiff({ sections, currentArt, proposedArt }: {
  sections: DiffSection[]; currentArt: ReactNode; proposedArt: ReactNode
}) {
  return <div className={styles.diff}>
    <p><span className={styles.removed}>− Removed</span> · <span className={styles.added}>+ Added</span></p>
    <div className={styles.pictures}>
      <figure><div>{currentArt}</div><figcaption>Current recipe</figcaption></figure>
      <figure><div>{proposedArt}</div><figcaption>Proposed recipe</figcaption></figure>
    </div>
    {sections.map((section) => <section key={section.title}>
      <h2>{section.title}</h2>
      <ul className={styles.lines}>{section.lines.map((line, index) => <li key={index} className={styles[line.kind]}>
        <span aria-label={line.kind === 'removed' ? 'Removed' : line.kind === 'added' ? 'Added' : undefined}>
          {line.kind === 'removed' ? '−' : line.kind === 'added' ? '+' : ''}
        </span>
        {line.kind === 'removed' ? <del>{line.text}</del> : line.kind === 'added' ? <ins>{line.text}</ins> : <span>{line.text}</span>}
      </li>)}</ul>
    </section>)}
  </div>
}
