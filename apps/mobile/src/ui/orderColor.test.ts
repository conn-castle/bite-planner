import { describe, expect, it } from 'vitest'
import { orderColor } from './orderColor'

describe('orderColor', () => {
  it('gives consecutive orders different colors, and starts over after the fifth', () => {
    expect(orderColor(1)).toBe('var(--cc-order-1)')
    expect(orderColor(2)).toBe('var(--cc-order-2)')
    expect(orderColor(5)).toBe('var(--cc-order-5)')
    expect(orderColor(6)).toBe('var(--cc-order-1)')
  })

  it("gives the order's soft and deep tones of the same color", () => {
    expect(orderColor(2, 'soft')).toBe('var(--cc-order-2-soft)')
    expect(orderColor(7, 'deep')).toBe('var(--cc-order-2-deep)')
  })
})
