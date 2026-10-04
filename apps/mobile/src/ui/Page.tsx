import {
  IonButtons,
  IonContent,
  IonFooter,
  IonHeader,
  IonPage,
  IonRefresher,
  IonRefresherContent,
  IonTitle,
  IonToolbar,
  type RefresherEventDetail,
  useIonRouter,
} from '@ionic/react'
import { type MouseEvent, type ReactNode, useState } from 'react'
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
  children: ReactNode
}

/** The frame of every tab's screen: a large title on the warm page, content that clears the tab bar. */
export function Page({ title, overline, actions, onRefresh, children }: PageProps) {
  async function refresh(event: CustomEvent<RefresherEventDetail>) {
    try {
      await onRefresh?.()
    } finally {
      event.detail.complete()
    }
  }

  return (
    <IonPage>
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
  /** Shown in the toolbar, always (a review decision over the mockup's title revealed on scroll). */
  title: string
  /**
   * Pushed in a tab rather than a nav: its content clears the tab bar, and
   * Back goes to `href` (e.g. Orders at /week) when there's no page to go
   * back to (it was opened directly). With a `backLabel`, it has the
   * navigation bar of the mockup's pushed pages (spec/mockup.html: .navbar):
   * that label on Back, and the title always shown. Otherwise, and in a nav,
   * a round Back button (.hero-btns).
   */
  tab?: { href: string; backLabel?: string }
  /** Controls at the end of the toolbar, e.g. a meal's rating. */
  actions?: ReactNode
  /** The page's main action, kept along the bottom (above the tab bar) as the content scrolls under it. */
  footer?: ReactNode
  children: ReactNode
}

/**
 * A screen pushed on top of another, with a Back button: onto an `IonNav`
 * (e.g. a recipe inside the create-order card), whose Back pops it; or onto a
 * tab's route (e.g. an order opened from Orders), whose Back goes back in the
 * router. The nav makes its page's element; a route's page wraps this in an
 * `IonPage`, which registers with the tab's router outlet. (An `IonPage` in a
 * nav would register with the outlet too, which then hides it.)
 */
export function NavPage({ title, tab, actions, footer, children }: NavPageProps) {
  const bar = tab?.backLabel
  // Once content scrolls under the toolbar, the toolbar shows its glass and title.
  const [scrolled, setScrolled] = useState(false)
  const router = useIonRouter()

  function back(event: MouseEvent<HTMLElement>) {
    const nav = event.currentTarget.closest('ion-nav')
    if (nav) void nav.pop()
    else if (router.canGoBack()) router.goBack()
    else router.push(tab?.href ?? '/', 'back')
  }

  return (
    <>
      <IonHeader translucent className={bar ? 'cc-bar' : scrolled ? 'cc-scrolled' : undefined}>
        <IonToolbar className={bar ? styles.barToolbar : styles.navToolbar}>
          <IonButtons slot="start">
            {bar ? (
              <button type="button" className={`${styles.back} cc-hit`} onClick={back}>
                <Icon name="chevL" />
                <span>{bar}</span>
              </button>
            ) : (
              <IconButton variant="fill" aria-label="Back" onClick={back}>
                <Icon name="chevL" />
              </IconButton>
            )}
          </IonButtons>
          <IonTitle>{title}</IonTitle>
          {actions && <IonButtons slot="end">{actions}</IonButtons>}
        </IonToolbar>
      </IonHeader>
      <IonContent fullscreen scrollEvents onIonScroll={(event) => setScrolled(event.detail.scrollTop > 8)}>
        <div className={bar ? styles.tabBody : `${styles.navBody} ${tab && !footer ? styles.clearTabBar : ''}`}>{children}</div>
      </IonContent>
      {footer && (
        <IonFooter className={`${styles.footer} ${tab ? styles.aboveTabBar : ''}`}>
          <div className={styles.footerBar}>{footer}</div>
        </IonFooter>
      )}
    </>
  )
}
