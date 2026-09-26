import { App } from '@capacitor/app'
import { useIonRouter } from '@ionic/react'
import { useEffect } from 'react'
import { Toast } from '../../ui/Toast'
import { dismissToast, pushAvailable, setUpPush, tapToast, useTapped, useToast } from './notifications'

/**
 * The signed-in app's side of notifications: keeps this phone registered (at
 * launch and on each return to the foreground), shows one that arrives while
 * the app is open, and takes a tap on one to Home, which opens what it's about
 * (see WeekPage).
 */
export function PushHost() {
  const router = useIonRouter()
  const toast = useToast()
  const tapped = useTapped()

  useEffect(() => {
    if (!pushAvailable) return
    void setUpPush()
    const listener = App.addListener('appStateChange', ({ isActive }) => {
      if (isActive) void setUpPush()
    })
    return () => void listener.then((handle) => handle.remove())
  }, [])

  useEffect(() => {
    if (tapped) router.push('/week', 'root')
  }, [tapped, router])

  if (!toast) return null
  return (
    <Toast key={toast.id} title={toast.title} body={toast.body} onTap={() => tapToast(toast)} onHide={dismissToast} />
  )
}
