import { IonIcon, IonLabel, IonRouterOutlet, IonTabBar, IonTabButton, IonTabs } from '@ionic/react'
import {
  calendarClear,
  calendarClearOutline,
  settings,
  settingsOutline,
  time,
  timeOutline,
} from 'ionicons/icons'
import { Navigate, Route, useLocation } from 'react-router-dom'
import { HistoryPage } from '../features/history/HistoryPage'
import { RequestPage } from '../features/history/RequestPage'
import { MealPage } from '../features/meals/MealPage'
import { SettingsPage } from '../features/settings/SettingsPage'
import { WeekPage } from '../features/week/WeekPage'

const TABS = [
  { tab: 'week', label: 'This week', icon: calendarClearOutline, selectedIcon: calendarClear },
  { tab: 'history', label: 'History', icon: timeOutline, selectedIcon: time },
  { tab: 'settings', label: 'Settings', icon: settingsOutline, selectedIcon: settings },
]

/** The signed-in app: three tabs, each with its own navigation stack. */
export function Tabs() {
  const current = useLocation().pathname.split('/')[1]

  return (
    <IonTabs>
      <IonRouterOutlet>
        <Route path="/week" element={<WeekPage />} />
        <Route path="/history" element={<HistoryPage />} />
        <Route path="/history/:jobId" element={<RequestPage />} />
        <Route path="/history/:jobId/meals/:mealId" element={<MealPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/week" replace />} />
      </IonRouterOutlet>
      <IonTabBar slot="bottom">
        {TABS.map(({ tab, label, icon, selectedIcon }) => (
          <IonTabButton key={tab} tab={tab} href={`/${tab}`}>
            <IonIcon aria-hidden="true" icon={current === tab ? selectedIcon : icon} />
            <IonLabel>{label}</IonLabel>
          </IonTabButton>
        ))}
      </IonTabBar>
    </IonTabs>
  )
}
