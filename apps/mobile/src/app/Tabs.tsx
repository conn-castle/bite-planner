import { IonLabel, IonPage, IonRouterOutlet, IonTabBar, IonTabButton, IonTabs } from '@ionic/react'
import { Navigate, Route, useParams } from 'react-router-dom'
import { HistoryPage } from '../features/history/HistoryPage'
import { MealPage } from '../features/meals/MealPage'
import { SettingsPage } from '../features/settings/SettingsPage'
import { OrderPage } from '../features/week/OrderPage'
import { WeekPage } from '../features/week/WeekPage'
import { Icon, type IconName } from '../ui/Icon'

const TABS: { tab: string; label: string; icon: IconName }[] = [
  { tab: 'week', label: 'Orders', icon: 'stack' },
  { tab: 'history', label: 'History', icon: 'clock' },
  { tab: 'settings', label: 'Settings', icon: 'sliders' },
]

/** The signed-in app: three tabs, each with its own navigation stack. */
export function Tabs() {
  return (
    <IonTabs>
      <IonRouterOutlet>
        <Route path="/week" element={<WeekPage />} />
        {/* Pages pushed in the Orders tab; NavPage explains the IonPage around them. */}
        <Route
          path="/week/orders/:orderId"
          element={
            <IonPage>
              <OrderPage />
            </IonPage>
          }
        />
        <Route
          path="/week/meals/:mealId"
          element={
            <IonPage>
              <RoutedMeal />
            </IonPage>
          }
        />
        <Route path="/history" element={<HistoryPage />} />
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
  )
}

/** A recipe opened from a route: its meal is in the path. */
function RoutedMeal() {
  const { mealId } = useParams<{ mealId: string }>()
  return mealId ? <MealPage mealId={mealId} tab={{ href: '/week' }} /> : <Navigate to="/week" replace />
}
