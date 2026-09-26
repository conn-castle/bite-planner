import type { OrderStep } from '../../lib/api/types'

/** The create-order flow's steps, in order: its progress dots, and where a lock's holder is. */
const FLOW_STEPS: readonly { step: OrderStep; name: string }[] = [
  { step: 'pick', name: 'Pick meals' },
  { step: 'dates', name: 'Dates and servings' },
]

export const STEP_NAMES = FLOW_STEPS.map(({ name }) => name)

/** A step's place in the flow (0-based). */
export function stepIndex(step: OrderStep): number {
  return FLOW_STEPS.findIndex((flowStep) => flowStep.step === step)
}
