import type { CSSProperties } from 'react'

// The order colors defined in theme/tokens.css (--bp-order-1 …, with -soft and -deep tones).
const ORDER_COLORS = 5

/** An order's color, a CSS value: orders take the colors in turn by number. */
export function orderColor(orderNumber: number, tone: 'base' | 'soft' | 'deep' = 'base'): string {
  const index = ((orderNumber - 1) % ORDER_COLORS) + 1
  return `var(--bp-order-${index}${tone === 'base' ? '' : `-${tone}`})`
}

type OrderColors = CSSProperties & { '--c': string; '--soft': string; '--deep': string }

/** An order's colors as CSS variables, for anything drawn in them (its card, its track): --c, --soft, --deep. */
export function orderColors(orderNumber: number): OrderColors {
  return {
    '--c': orderColor(orderNumber),
    '--soft': orderColor(orderNumber, 'soft'),
    '--deep': orderColor(orderNumber, 'deep'),
  }
}
