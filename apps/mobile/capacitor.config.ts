import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.conncastle.castlechef',
  appName: 'Castle Chef',
  webDir: 'dist',
  // The app is light only: white behind the WebView, dark status bar text.
  backgroundColor: '#ffffff',
  experimental: { ios: { spm: { packageOptions: {
    '@capacitor-community/keep-awake': { symlink: true },
    '@capacitor/app': { symlink: true },
    '@capacitor/app-launcher': { symlink: true },
    '@capacitor/device': { symlink: true },
    '@capacitor/haptics': { symlink: true },
    '@capacitor/keyboard': { symlink: true },
    '@capacitor/local-notifications': { symlink: true },
    '@capacitor/network': { symlink: true },
    '@capacitor/preferences': { symlink: true },
    '@capacitor/push-notifications': { symlink: true },
    '@capacitor/splash-screen': { symlink: true },
    '@capacitor/status-bar': { symlink: true },
  } } } },
  plugins: {
    // Hidden by the app once its first screen has rendered (src/app/native.ts).
    SplashScreen: { launchAutoHide: false, backgroundColor: '#ffffff' },
    StatusBar: { style: 'LIGHT' },
    // No banner while the app is open: it shows its own (features/notifications).
    PushNotifications: { presentationOptions: [] },
  },
};

export default config;
