import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.conncastle.castlechef',
  appName: 'Castle Chef',
  webDir: 'dist',
  // The app is light only: white behind the WebView, dark status bar text.
  backgroundColor: '#ffffff',
  plugins: {
    // Hidden by the app once its first screen has rendered (src/app/native.ts).
    SplashScreen: { launchAutoHide: false, backgroundColor: '#ffffff' },
    StatusBar: { style: 'LIGHT' },
    // No banner while the app is open: it shows its own (features/notifications).
    PushNotifications: { presentationOptions: [] },
  },
};

export default config;
