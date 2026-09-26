import { IonLabel, IonPage, IonRouterOutlet, IonTabBar, IonTabButton, IonTabs } from '@ionic/react'
import { Navigate, Route, useParams, useSearchParams } from 'react-router-dom'
import { HistoryPage } from '../features/history/HistoryPage'
import { MealPage } from '../features/meals/MealPage'
import { PushHost } from '../features/notifications/PushHost'
import { SettingsPage } from '../features/settings/SettingsPage'
import { SERVINGS } from '../lib/api/limits'
import { OrderPage } from '../features/week/OrderPage'
import { WeekPage } from '../features/week/WeekPage'
import { Icon, type IconName } from '../ui/Icon'
import { useWarmCaches } from './warm'

const TABS: { tab: string; label: string; icon: IconName }[] = [
  { tab: 'week', label: 'Orders', icon: 'stack' },
  { tab: 'history', label: 'History', icon: 'clock' },
  { tab: 'settings', label: 'Settings', icon: 'sliders' },
]

/** The signed-in app: three tabs, each with its own navigation stack. */
export function Tabs() {
  useWarmCaches()
  return (
    <>
      <PushHost />
      <IonTabs>
        <IonRouterOutlet>
          <Route path="/week" element={<WeekPage />} />
          {/* Pages pushed in the Orders and History tabs; NavPage explains the IonPage around them. */}
          <Route
            path="/week/orders/:orderId"
            element={
              <IonPage>
                <OrderPage tab={{ href: '/week', backLabel: 'Orders' }} />
              </IonPage>
            }
          />
          <Route
            path="/week/meals/:mealId"
            element={
              <IonPage>
                <RoutedMeal href="/week" />
              </IonPage>
            }
          />
          <Route path="/history" element={<HistoryPage />} />
          <Route
            path="/history/orders/:orderId"
            element={
              <IonPage>
                <OrderPage tab={{ href: '/history', backLabel: 'History' }} />
              </IonPage>
            }
          />
          <Route
            path="/history/meals/:mealId"
            element={
              <IonPage>
                <RoutedMeal href="/history" />
              </IonPage>
            }
          />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/week" replace />} />
        </IonRouterOutlet>
        <IonTabBar slot="bottom">
          {TABS.map(({ tab, label, icon }) => (
            <IonTabButton key={tab} tab={tab} href={`/${tab}`}>
              <Icon name={icon} />
              <IonLabel>{label}</IonLabel>
            </IonTabButton>
          ))}
        </IonTabBar>
      </IonTabs>
    </>
  )
}

/**
 * A recipe opened from a tab's route (a meal of a placed order): its meal is in
 * the path, and the servings ordered in `?servings=`.
 */
function RoutedMeal({ href }: { href: string }) {
  const { mealId } = useParams<{ mealId: string }>()
  const [params] = useSearchParams()
  return mealId ? (
    <MealPage mealId={mealId} tab={{ href }} rateable servings={orderedServings(params.get('servings'))} />
  ) : (
    <Navigate to={href} replace />
  )
}

/**
 * The servings ordered, from a route: a meal of an order that doesn't say (or
 * says something that isn't a pick's servings) starts at the recipe's own.
 */
function orderedServings(value: string | null): number | null {
  const servings = Number(value)
  return value !== null && Number.isInteger(servings) && servings >= SERVINGS.min && servings <= SERVINGS.max
    ? servings
    : null
}
