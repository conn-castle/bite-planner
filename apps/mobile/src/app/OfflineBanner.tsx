import { IonIcon } from '@ionic/react'
import { onlineManager } from '@tanstack/react-query'
import { cloudOfflineOutline } from 'ionicons/icons'
import { useSyncExternalStore } from 'react'
import styles from './OfflineBanner.module.css'

/** A floating note while the device is offline; the app keeps showing saved data. */
export function OfflineBanner() {
  const online = useSyncExternalStore(onlineManager.subscribe, () => onlineManager.isOnline())
  if (online) return null
  return (
    <div className={styles.banner} role="status">
      <IonIcon icon={cloudOfflineOutline} aria-hidden="true" />
      Offline · showing saved meals
    </div>
  )
}
