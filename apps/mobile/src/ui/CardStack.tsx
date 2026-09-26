import type { ReactNode } from 'react'
import styles from './CardStack.module.css'

/**
 * Cards one under another (spec/mockup.html: .stack), e.g. the orders on Home.
 * On a wider screen they sit side by side, two or three to a row; a phone
 * always has one column.
 */
export function CardStack({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={`${styles.stack} ${className ?? ''}`}>{children}</div>
}
