import { describe, expect, it } from 'vitest'
import type { Job, ShoppingItem, ShoppingList } from '../../lib/api/types'
import { forMeals, itemAmount, listCounts, listState, quantityStep, spokenList } from './list'

const job = (status: Job['status']): Job => ({
  id: 'build',
  kind: 'build_list',
  status,
  error: status === 'failed' ? 'The agent gave up.' : null,
  requested_by: { user_id: 'me', name: 'Nick' },
  created_at: '2026-09-24T09:00:00Z',
  meals: [],
})
const list = (status: Job['status'], current: boolean): ShoppingList => ({ job: job(status), current, items: [] })
const item = (fields: Partial<ShoppingItem>): ShoppingItem => ({
  id: 'i',
  name: 'yellow onions',
  quantity: 2,
  unit: null,
  aisle: 'produce',
  meal_ids: [],
  on_hand: false,
  removed: false,
  ...fields,
})

describe('listState', () => {
  it('is ready only when the list was made for the picks now', () => {
    expect(listState(list('succeeded', true))).toBe('ready')
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

  it('says the items assumed on hand as a sentence', () => {
    expect(spokenList(['salt'])).toBe('salt')
    expect(spokenList(['salt', 'pepper'])).toBe('salt and pepper')
    expect(spokenList(['salt', 'pepper', 'olive oil'])).toBe('salt, pepper, and olive oil')
  })
})
