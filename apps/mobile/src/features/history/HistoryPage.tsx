import { Page } from '../../ui/Page'
import styles from './HistoryPage.module.css'

/** Past orders. Placed orders aren't listed yet, so this is always empty for now. */
export function HistoryPage() {
  return (
    <Page title="History">
      <h2 className={styles.empty}>Past orders and their meals will show here.</h2>
    </Page>
  )
}
