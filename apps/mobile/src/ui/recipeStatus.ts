import type { RecipeChange } from '../lib/api/types'
import type { IconName } from './Icon'

interface RecipeStatus {
  label: string
  icon: IconName
}

const STATUSES: Record<RecipeChange['state'], RecipeStatus> = {
  running: { label: 'Still changing', icon: 'hourglass' },
  ready: { label: 'Ready to review', icon: 'question' },
  failed: { label: 'Change failed · decide', icon: 'question' },
}

/** One label and icon for the projected queued/running, ready and failed states. */
export function recipeChangeStatus(state: RecipeChange['state']): RecipeStatus {
  return STATUSES[state]
}
