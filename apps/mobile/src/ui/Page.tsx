import {
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
import { type ReactNode, type RefObject, useState } from 'react'
import { IconButton } from './Button'
import { Icon } from './Icon'
import styles from './Page.module.css'

interface PageProps {
  /** The large title at the top. */
  title: string
  /** A small line over the title, e.g. today's date. */
  overline?: string
  /** Controls at the end of the title's line, e.g. a round New order button. */
  actions?: ReactNode
  /** Enables pull to refresh. */
  onRefresh?: () => Promise<unknown>
  /** The page's element, e.g. to present a card modal over it. */
  pageRef?: RefObject<HTMLElement | null>
  children: ReactNode
}

/** The frame of every tab's screen: a large title on the warm page, content that clears the tab bar. */
export function Page({ title, overline, actions, onRefresh, pageRef, children }: PageProps) {
  async function refresh(event: CustomEvent<RefresherEventDetail>) {
    try {
      await onRefresh?.()
    } finally {
      event.detail.complete()
    }
  }

  return (
    <IonPage ref={pageRef}>
      <IonContent className={styles.content}>
        {onRefresh && (
          <IonRefresher slot="fixed" onIonRefresh={(event) => void refresh(event)}>
            <IonRefresherContent />
          </IonRefresher>
        )}
        {/* Content fades out under the status bar. */}
        <div slot="fixed" className={styles.fade} aria-hidden="true" />
        <header className={styles.header}>
          {overline && <p className={styles.overline}>{overline}</p>}
          <div className={styles.titleRow}>
            <h1 className={styles.title}>{title}</h1>
            {actions}
          </div>
        </header>
        <div className={styles.body}>{children}</div>
      </IonContent>
    </IonPage>
  )
}

interface NavPageProps {
  /** Shown in the toolbar once the page's own heading scrolls away. */
  title: string
  children: ReactNode
}

/**
 * A screen pushed onto an `IonNav` (e.g. a recipe inside the create-order
 * card), with a Back button that pops it. The nav makes the page element and
 * shows it; an `IonPage` here would instead register with the tab bar's
 * router outlet, which then decides whether the page is visible.
 */
export function NavPage({ title, children }: NavPageProps) {
  // Once content scrolls under the toolbar, the toolbar shows its glass and title.
  const [scrolled, setScrolled] = useState(false)

  return (
    <>
      <IonHeader translucent className={scrolled ? 'cc-scrolled' : undefined}>
        <IonToolbar className={styles.navToolbar}>
          <IonButtons slot="start">
            <IconButton
              variant="fill"
              aria-label="Back"
              onClick={(event) => void event.currentTarget.closest('ion-nav')?.pop()}
            >
              <Icon name="chevL" />
            </IconButton>
          </IonButtons>
          <IonTitle className="cc-reveal">{title}</IonTitle>
        </IonToolbar>
      </IonHeader>
      <IonContent fullscreen scrollEvents onIonScroll={(event) => setScrolled(event.detail.scrollTop > 8)}>
        <div className={styles.navBody}>{children}</div>
      </IonContent>
    </>
  )
}
