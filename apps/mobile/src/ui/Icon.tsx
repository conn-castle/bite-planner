import type { ReactNode } from 'react'
import styles from './Icon.module.css'

// spec/mockup.html's icon set (its `I`): 24×24 strokes, drawn in the text color.
const PATHS = {
  stack: (
    <>
      <rect x="3" y="9" width="18" height="12" rx="3" />
      <path d="M6 6h12M8.5 3h7" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  sliders: (
    <>
      <path d="M4 7h9M17 7h3M4 17h3M11 17h9" />
      <circle cx="15" cy="7" r="2" />
      <circle cx="9" cy="17" r="2" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  chevL: <path d="M15 5l-7 7 7 7" />,
  chevR: <path d="M9 5l7 7-7 7" />,
  chevD: <path d="M5 9l7 7 7-7" />,
  truck: (
    <>
      <path d="M2.5 6.5h11v9.5h-11zM13.5 9.5h4l3 3.5v3h-7" />
      <circle cx="6.5" cy="17" r="1.8" />
      <circle cx="16.8" cy="17" r="1.8" />
    </>
  ),
  bag: <path d="M5 8h14l-1 12H6L5 8zM9 8V6.5a3 3 0 016 0V8" />,
  bell: <path d="M6 16v-5a6 6 0 0112 0v5l1.5 2h-15L6 16zM10 20.5a2 2 0 004 0" />,
  alert: <path d="M12 3.5l9.5 16.5h-19zM12 10v4.5M12 17.5v.01" />,
  cal: (
    <>
      <rect x="3.5" y="5" width="17" height="15.5" rx="3" />
      <path d="M3.5 10h17M8 3v4M16 3v4" />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="10.5" width="14" height="10" rx="2.5" />
      <path d="M8.5 10.5V8a3.5 3.5 0 017 0v2.5" />
    </>
  ),
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  spark: (
    <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9zM19 16l.7 1.8 1.8.7-1.8.7L19 21l-.7-1.8-1.8-.7 1.8-.7z" />
  ),
  x: <path d="M6 6l12 12M18 6L6 18" />,
  users: (
    <>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2.5 20c.5-3.5 3-5.5 6.5-5.5s6 2 6.5 5.5M16 4.8a3.5 3.5 0 010 6.4M18 14.6c2 .6 3.3 2.4 3.5 5.4" />
    </>
  ),
} satisfies Record<string, ReactNode>

export type IconName = keyof typeof PATHS

/**
 * One of the app's icons, sized by the surrounding font size (1em). Set its
 * size with `font-size` and, where the design is bolder, `stroke-width`.
 */
export function Icon({ name, className }: { name: IconName; className?: string }) {
  return (
    <svg className={className ? `${styles.icon} ${className}` : styles.icon} viewBox="0 0 24 24" aria-hidden="true">
      {PATHS[name]}
    </svg>
  )
}
