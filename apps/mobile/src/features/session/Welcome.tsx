import { IonContent, IonPage } from '@ionic/react'
import type { ReactNode } from 'react'
import { BrandMark } from '../../ui/art'
import styles from './Welcome.module.css'

interface WelcomeProps {
  title: string
  subtitle: ReactNode
  children: ReactNode
}

/**
 * The full-screen frame for signing in and other screens outside the app's
 * tabs: the app icon, a title and subtitle, centered, then the form or actions.
 */
export function Welcome({ title, subtitle, children }: WelcomeProps) {
  return (
    <IonPage>
      <IonContent>
        <div className={styles.welcome}>
          <header className={styles.header}>
            <BrandMark size={72} />
            <h1 className={styles.title}>{title}</h1>
            <p className={styles.subtitle}>{subtitle}</p>
          </header>
          <div className={styles.body}>{children}</div>
        </div>
      </IonContent>
    </IonPage>
  )
}
