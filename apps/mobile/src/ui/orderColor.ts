// The order colors defined in theme/tokens.css (--cc-order-1 …).
const ORDER_COLORS = 5

/** An order's color, a CSS value: orders take the colors in turn by number. */
export function orderColor(orderNumber: number): string {
  return `var(--cc-order-${((orderNumber - 1) % ORDER_COLORS) + 1})`
}
