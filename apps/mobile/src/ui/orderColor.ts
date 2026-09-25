// The order colors defined in theme/tokens.css (--cc-order-1 …, with -soft and -deep tones).
const ORDER_COLORS = 5

/** An order's color, a CSS value: orders take the colors in turn by number. */
export function orderColor(orderNumber: number, tone: 'base' | 'soft' | 'deep' = 'base'): string {
  const index = ((orderNumber - 1) % ORDER_COLORS) + 1
  return `var(--cc-order-${index}${tone === 'base' ? '' : `-${tone}`})`
}
