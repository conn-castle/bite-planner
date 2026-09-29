import { describe, expect, it } from 'vitest'
import type { Product } from './api/types'
import { formatMoney, productCost, productSize } from './products'

const ONIONS: Product = {
  product_id: '17499438',
  name: 'Wegmans Onions, Yellow',
  size: '2 lb',
  price_cents: 229,
  price_per_lb_cents: null,
  estimated_lb: null,
  image_url: null,
  bought_before: true,
  count: 2,
}
const THIGHS: Product = {
  ...ONIONS,
  name: 'Wegmans Boneless, Skinless Chicken Thighs, Family Pack',
  size: 'per lb',
  price_cents: 1592,
  price_per_lb_cents: 379,
  estimated_lb: 4.2,
  count: 1,
}

describe('store products', () => {
  it('shows money with cents and thousands', () => {
    expect([formatMoney(229), formatMoney(5), formatMoney(123400)]).toEqual(['$2.29', '$0.05', '$1,234.00'])
  })

  it('shows a size, or a weighed item’s price per pound and weight', () => {
    expect(productSize(ONIONS)).toBe('2 lb')
    expect(productSize(THIGHS)).toBe('$3.79/lb · about 4.2 lb')
  })

  it('shows what the count costs, or nothing without a price', () => {
    expect(productCost(ONIONS)).toBe('$4.58')
    expect(productCost({ ...ONIONS, price_cents: null })).toBeNull()
  })
})
