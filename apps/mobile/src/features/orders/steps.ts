import type { OrderStep } from '../../lib/api/types'

/**
 * The create-order flow's steps, in order, each doing one thing: its progress
 * dots, and where a lock's holder is (what they're doing, for the others).
 */
const FLOW_STEPS: readonly { step: OrderStep; name: string; doing: string }[] = [
  { step: 'pick', name: 'Pick meals', doing: 'picking meals' },
  { step: 'servings', name: 'Servings', doing: 'choosing the servings' },
  { step: 'delivery', name: 'Delivery date', doing: 'choosing the delivery date' },
  { step: 'next', name: 'Next delivery', doing: 'choosing the next delivery date' },
  { step: 'review', name: 'Review', doing: 'reviewing the order' },
]

export const STEP_NAMES = FLOW_STEPS.map(({ name }) => name)

/** A step's place in the flow (0-based). */
export function stepIndex(step: OrderStep): number {
  return FLOW_STEPS.findIndex((flowStep) => flowStep.step === step)
}

/** What a member at this step is doing: "picking meals". */
export function stepDoing(step: OrderStep): string {
  return FLOW_STEPS[stepIndex(step)].doing
}
