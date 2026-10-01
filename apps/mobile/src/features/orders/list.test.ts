import { describe, expect, it } from 'vitest'
import type { Job, Product, ShoppingItem, ShoppingList } from '../../lib/api/types'
import { forMeals, itemAmount, listCounts, listState, productTotals, quantityStep, readyList, totalShown, undecided } from './list'

const job = (status: Job['status']): Job => ({
  id: 'build',
  kind: 'build_list',
  status,
  error: status === 'failed' ? 'The agent gave up.' : null,
  requested_by: { user_id: 'me', name: 'Nick' },
  created_at: '2026-09-24T09:00:00Z',
  meals: [],
})
const shop = (status: Job['status']): Job => ({ ...job(status), id: 'shop', kind: 'shop' })
const list = (status: Job['status'], current: boolean, shopped: Job['status'] | null = 'succeeded'): ShoppingList => ({
  job: job(status),
  current,
  shop: shopped === null ? null : shop(shopped),
  store: 'Wegmans',
  items: [],
})
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
const item = (fields: Partial<ShoppingItem>): ShoppingItem => ({
  id: 'i',
  name: 'yellow onions',
  quantity: 2,
  recipe_quantity: 2,
  unit: null,
  aisle: 'produce',
  meal_ids: [],
  on_hand: false,
  removed: false,
  product: ONIONS,
  alternatives: [],
  no_alternatives_reason: null,
  not_found: false,
  skippable_reason: null,
  ...fields,
})

describe('listState', () => {
  it('is ready only when the list was made for the picks now and shopped', () => {
    expect(listState(list('succeeded', true))).toBe('ready')
  })

  it('waits while the list is shopped, and says when shopping failed', () => {
    expect(listState(list('succeeded', true, 'pending'))).toBe('shopping')
    expect(listState(list('succeeded', true, 'running'))).toBe('shopping')
    expect(listState(list('succeeded', true, 'failed'))).toBe('shopFailed')
  })

  it('shops a list made for the picks now that was never shopped', () => {
    expect(listState(list('succeeded', true, null))).toBe('shopStart')
  })

  it('waits while one is being made, and says when the newest failed', () => {
    expect(listState(list('pending', false))).toBe('building')
    expect(listState(list('running', false))).toBe('building')
    expect(listState(list('failed', false))).toBe('failed')
  })

  it('starts one when there is none, or only one for other picks', () => {
    expect(listState(null)).toBe('start')
    expect(listState(list('succeeded', false))).toBe('start')
  })
})

describe('an item as the list shows it', () => {
  it('reads its amount the way recipes do', () => {
    expect(itemAmount({ quantity: 2, unit: null })).toBe('2')
    expect(itemAmount({ quantity: 1.5, unit: 'cup' })).toBe('1½ cups')
    expect(itemAmount({ quantity: 0.75, unit: 'lb' })).toBe('¾ lb')
    expect(itemAmount({ quantity: null, unit: null })).toBe('')
  })

  it('steps a counted item by one, and small amounts finely', () => {
    expect(quantityStep({ quantity: 3, unit: null })).toBe(1)
    expect(quantityStep({ quantity: 0.75, unit: 'lb' })).toBe(0.25)
    expect(quantityStep({ quantity: 3, unit: 'cup' })).toBe(0.5)
    expect(quantityStep({ quantity: 16, unit: 'oz' })).toBe(1)
  })

  it('names the meals it is for', () => {
    const names = new Map([
      ['a', 'Fajitas'],
      ['b', 'Curry'],
    ])
    expect(forMeals({ meal_ids: ['a', 'b'] }, names)).toBe('for Fajitas, Curry')
    expect(forMeals({ meal_ids: ['gone'] }, names)).toBe('')
  })
})

describe('the list as a whole', () => {
  it('counts what is ordered and what was taken off, never the items assumed on hand', () => {
    const items = [item({ id: '1' }), item({ id: '2', removed: true }), item({ id: '3', name: 'salt', on_hand: true })]
    expect(listCounts(items)).toEqual({ ordered: 1, removed: 1 })
  })
})

describe('the shopped list', () => {
  const missing = item({ id: 'curry', name: 'curry leaves', product: null, not_found: true })

  it('has the items the store does not have to decide on, until one is picked or skipped', () => {
    expect(undecided([item({}), missing]).map((i) => i.id)).toEqual(['curry'])
    expect(undecided([{ ...missing, removed: true }])).toEqual([])
    expect(undecided([{ ...missing, product: ONIONS }])).toEqual([])
  })

  it('is ready to go on only once they are all decided', () => {
    const shopped = { ...list('succeeded', true), items: [item({}), missing] }
    expect(readyList({ shopping_list: shopped })).toBeNull()
    expect(readyList({ shopping_list: { ...shopped, items: [item({})] } })).not.toBeNull()
  })

  it('is not ready to go on with nothing left to buy', () => {
    const shopped = { ...list('succeeded', true), items: [item({ removed: true }), item({ id: 'x', on_hand: true })] }
    expect(readyList({ shopping_list: shopped })).toBeNull()
  })

  it('totals the products bought, not the ones skipped', () => {
    const items = [item({}), item({ id: 'x', removed: true }), item({ id: 'y', on_hand: true, product: null })]
    expect(productTotals(items)).toEqual({ count: 1, cents: 458, unpriced: 0 })
    expect(totalShown(productTotals(items))).toBe('$4.58')
  })

  it('shows a total that leaves out products without a price as only part of it', () => {
    const totals = productTotals([item({}), item({ id: 'z', product: { ...ONIONS, price_cents: null } })])
    expect(totals).toEqual({ count: 2, cents: 458, unpriced: 1 })
    expect(totalShown(totals)).toBe('$4.58+')
  })
})
