import type { RecipeChange } from '../lib/api/types'
import { Icon } from './Icon'
import { recipeChangeStatus } from './recipeStatus'
import styles from './RecipeChangeStatus.module.css'

type RecipeChangeStatusProps = { state: RecipeChange['state'] } & (
  | { presentation: 'badge' }
  | { presentation: 'caption' }
  | { presentation?: 'action'; mealName: string; onOpen: () => void }
)

/** Noninteractive row cues belong to the meal-opening button; grouped guards have their own action. */
export function RecipeChangeStatus(props: RecipeChangeStatusProps) {
  const { label, icon } = recipeChangeStatus(props.state)
  if (props.presentation === 'badge') return <span className={styles.badge} aria-hidden="true"><Icon name={icon} className={styles.badgeIcon} /></span>
  if (props.presentation === 'caption') return <span className={styles.caption}>{label}</span>
  return <button type="button" className={styles.status} onClick={props.onOpen} aria-label={`${label}: ${props.mealName}. Open recipe`}>
    <Icon name={icon} className={styles.symbol} />
    <span className={styles.text}><strong>{label}</strong><span>{props.mealName}</span></span>
    <Icon name="chevR" className={styles.chevron} />
  </button>
}
