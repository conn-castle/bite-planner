import {
  IonBackButton,
  IonButtons,
  IonContent,
  IonHeader,
  IonPage,
  IonRefresher,
  IonRefresherContent,
  IonTitle,
  IonToolbar,
  type RefresherEventDetail,
} from '@ionic/react'
import { chevronBack } from 'ionicons/icons'
import { type ReactNode, useState } from 'react'
import styles from './Page.module.css'

interface PageProps {
  /** Shown as the collapsing large title on tab roots, or in the toolbar once scrolled. */
  title: string
  /** Tab roots get the large title; pushed pages put their own heading in the body. */
  largeTitle?: boolean
  /** Pushed pages: where the back button goes when there is no history (a deep link). */
  backHref?: string
  /** Toolbar buttons at the end (e.g. "new request"). */
  actions?: ReactNode
  /** Enables pull to refresh. */
  onRefresh?: () => Promise<unknown>
  children: ReactNode
}

/** The frame of every screen: glass toolbar, optional large title, content that clears the tab bar. */
export function Page({ title, largeTitle = false, backHref, actions, onRefresh, children }: PageProps) {
  // Once content scrolls under the toolbar, the toolbar shows its edge (and, on
  // pushed pages, its title; tab roots hand the title over from the large one).
  const [scrolled, setScrolled] = useState(false)

  async function refresh(event: CustomEvent<RefresherEventDetail>) {
    try {
      await onRefresh?.()
    } finally {
      event.detail.complete()
    }
  }

  return (
    <IonPage>
      <IonHeader translucent className={scrolled ? 'cc-scrolled' : undefined}>
        <IonToolbar>
          {backHref && (
            <IonButtons slot="start">
              <IonBackButton defaultHref={backHref} text="" icon={chevronBack} />
            </IonButtons>
          )}
          <IonTitle className={largeTitle ? undefined : 'cc-reveal'}>{title}</IonTitle>
          {actions && <IonButtons slot="end">{actions}</IonButtons>}
        </IonToolbar>
      </IonHeader>
      <IonContent
        fullscreen
        scrollEvents
        onIonScroll={(event) => setScrolled(event.detail.scrollTop > 8)}
      >
        {onRefresh && (
          <IonRefresher slot="fixed" onIonRefresh={(event) => void refresh(event)}>
            <IonRefresherContent />
          </IonRefresher>
        )}
        {largeTitle && (
          <IonHeader collapse="condense">
            <IonToolbar>
              <IonTitle size="large">{title}</IonTitle>
            </IonToolbar>
          </IonHeader>
        )}
        <div className={styles.body}>{children}</div>
      </IonContent>
    </IonPage>
  )
}
