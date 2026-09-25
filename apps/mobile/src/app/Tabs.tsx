import { IonLabel, IonRouterOutlet, IonTabBar, IonTabButton, IonTabs } from '@ionic/react'
import { Navigate, Route } from 'react-router-dom'
import { HistoryPage } from '../features/history/HistoryPage'
import { SettingsPage } from '../features/settings/SettingsPage'
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
