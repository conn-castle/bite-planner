import { PotArt } from '../../ui/art'
import { EmptyState } from '../../ui/EmptyState'
import { Page } from '../../ui/Page'

/** Past orders. Placed orders aren't listed yet, so this is always empty for now. */
export function HistoryPage() {
  return (
    <Page title="History" largeTitle>
      <EmptyState art={<PotArt />} title="No past orders yet">
        Past orders and their meals will show here.
      </EmptyState>
    </Page>
  )
}
