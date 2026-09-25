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
import { type ReactNode, type RefObject, useState } from 'react'
import styles from './Page.module.css'

interface PageProps {
  /** Shown as the collapsing large title on tab roots, or in the toolbar once scrolled. */
  title: string
  /** Tab roots get the large title; pushed pages put their own heading in the body. */
  largeTitle?: boolean
  /** Toolbar buttons at the end (e.g. "new request"). */
  actions?: ReactNode
  /** Enables pull to refresh. */
  onRefresh?: () => Promise<unknown>
  /** The page's element, e.g. to present a card modal over it. */
  pageRef?: RefObject<HTMLElement | null>
  children: ReactNode
}

/** The frame of every tab's screen: glass toolbar, optional large title, content that clears the tab bar. */
export function Page({ pageRef, ...frame }: PageProps) {
  return (
    <IonPage ref={pageRef}>
      <PageFrame {...frame} back={false} bodyClassName={styles.body} />
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
  return <PageFrame title={title} back bodyClassName={styles.navBody}>{children}</PageFrame>
}

interface PageFrameProps extends Omit<PageProps, 'pageRef'> {
  /** Shows a Back button, which goes back in the nearest nav. */
  back: boolean
  bodyClassName: string
}

function PageFrame({ title, largeTitle = false, back, actions, onRefresh, bodyClassName, children }: PageFrameProps) {
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
    <>
      <IonHeader translucent className={scrolled ? 'cc-scrolled' : undefined}>
        <IonToolbar>
          {back && (
            <IonButtons slot="start">
              <IonBackButton text="" icon={chevronBack} aria-label="Back" />
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
        <div className={bodyClassName}>{children}</div>
      </IonContent>
    </>
  )
}
