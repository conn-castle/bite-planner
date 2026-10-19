import { useSyncExternalStore } from 'react'
import { onlineManager } from '@tanstack/react-query'
import { Button } from './Button'
import { InfoDialog } from './InfoDialog'
import { Notice } from './Notice'

export type ShoppingReplacement = 'build' | 'shop' | 'recipe'

/** Confirm only the action that replaces shopping work; ordinary draft edits stay reversible. */
export function ShoppingWorkDialog({ action, onClose, onConfirm, busy, saving, allowed, error }: {
  action: ShoppingReplacement | null
  onClose: () => void
  onConfirm: () => void
  busy: boolean
  saving: boolean
  allowed: boolean
  error: string | null
}) {
  const online: boolean = useSyncExternalStore(onlineManager.subscribe, () => onlineManager.isOnline())
  const label: string = action === 'recipe' ? 'Accept changes' : action === 'shop' ? 'Shop again' : 'Replace shopping list'
  return <InfoDialog isOpen={action !== null} onClose={onClose} dismissible={!busy} icon="info"
    title={action === 'recipe' ? 'Accept recipe changes?' : 'Replace shopping work?'} footer={<>
      <Button block onClick={() => { if (online && !saving && allowed && !busy) onConfirm() }} disabled={!online || saving || !allowed || busy}>
        {busy ? 'Saving…' : label}
      </Button>
      <Button block variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button>
    </>}>
    {action === 'recipe' && <p>This changes a selected recipe and can’t be undone.</p>}
    <p>{action === 'recipe'
      ? 'Shopping work made with the old recipe will need to be replaced. Any still running for it will stop.'
      : action === 'shop'
      ? 'Shopping again replaces the product choices for this list.'
      : 'Replacing the list resets quantities, removed items and product choices. Any shopping still running for the old list will stop.'}</p>
    {action === 'recipe' && <p>Replacing that list resets quantities, removed items and product choices.</p>}
    <p>Cancel keeps your existing work.</p>
    {!online && <Notice>You’re offline. Reconnect to continue, or Cancel to return.</Notice>}
    {saving && !busy && <Notice>Waiting for your changes to finish saving.</Notice>}
    {!allowed && <Notice>This action is no longer available. Cancel and refresh the order.</Notice>}
    {error && <Notice>Couldn't save that: {error}</Notice>}
  </InfoDialog>
}
