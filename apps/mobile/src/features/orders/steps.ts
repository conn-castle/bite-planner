import type { GroceryOrder, OrderStep } from '../../lib/api/types'
import { suggestions } from './batches'
import { shownDeliveryDate } from './dates'
import { readyList } from './list'

/**
 * The create-order flow's steps, in order, each doing one thing: its progress
 * dots, and where a lock's holder is (what they're doing, for the others).
 */
const FLOW_STEPS: Record<OrderStep, { name: string; doing: string }> = {
  pick: { name: 'Pick meals', doing: 'picking meals' },
  servings: { name: 'Servings', doing: 'choosing the servings' },
  list: { name: 'Shopping list', doing: 'checking the shopping list' },
  delivery: { name: 'Delivery', doing: 'choosing the delivery day and time' },
  next: { name: 'Next delivery', doing: 'choosing the next delivery date' },
  review: { name: 'Review', doing: 'reviewing the order' },
}

export const STEP_NAMES = Object.values(FLOW_STEPS).map(({ name }) => name)

/** A step's place in the flow (0-based). */
export function stepIndex(step: OrderStep): number {
  return Object.keys(FLOW_STEPS).indexOf(step)
}

/** What a member at this step is doing: "picking meals". */
export function stepDoing(step: OrderStep): string {
  return FLOW_STEPS[step].doing
}

/**
 * The step to show: the one the user went to (undefined: none yet, as the flow
 * opens: the furthest the order's flow has reached), unless the order can't
 * support it (e.g. another change removed every pick: picking meals; the list not
 * made, shopped, and decided for these picks: the list; no delivery date yet: that step).
 */
export function stepShown(order: GroceryOrder, chosen: OrderStep | undefined): OrderStep {
  const step = chosen ?? order.reached_step
  if (suggestions(order).length === 0 || order.picks.length === 0) return 'pick'
  const afterList: OrderStep[] = ['delivery', 'next', 'review']
  if (afterList.includes(step) && readyList(order) === null) return 'list'
  const afterDelivery: OrderStep[] = ['next', 'review']
  if (afterDelivery.includes(step) && shownDeliveryDate(order) === null) return 'delivery'
  return step
}
