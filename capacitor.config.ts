import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  // ─────────────────────────────────────────────────────────────────────────
  // App identity — must match the Bundle ID registered in App Store Connect.
  // Choose something unique. Once published, this CANNOT be changed.
  // ─────────────────────────────────────────────────────────────────────────
  appId: 'com.myra.languageteacher',
  appName: 'Myra Learns!',

  // ─────────────────────────────────────────────────────────────────────────
  // Remote server mode: the WKWebView loads the app directly from the Cloud
  // Run URL, so ALL existing relative API calls (/api/word, /api/tts, etc.)
  // resolve correctly without any frontend code changes.
  //
  // Replace the placeholder below with your actual Cloud Run service URL:
  //   gcloud run services describe dino-app --region=us-central1 \
  //     --format='value(status.url)'
  // ─────────────────────────────────────────────────────────────────────────
  server: {
    url: 'https://YOUR-CLOUD-RUN-URL.run.app',
    cleartext: false,  // HTTPS only — Cloud Run always uses HTTPS
  },

  // ─────────────────────────────────────────────────────────────────────────
  // iOS-specific configuration
  // ─────────────────────────────────────────────────────────────────────────
  ios: {
    // Match the app's background gradient start color so the launch screen
    // blends seamlessly into the app.
    backgroundColor: '#FFE4F0',

    // Minimum iOS version required for MediaRecorder + getUserMedia support.
    // Capacitor 6 sets this to 13 by default; 14.3 is needed for microphone.
    // Set this in Xcode → General → Minimum Deployments → iOS 14.3
  },
};

export default config;

// ─────────────────────────────────────────────────────────────────────────────
// SETUP INSTRUCTIONS (run on a Mac with Xcode installed):
//
//   1. Install dependencies:
//        npm install
//
//   2. Add iOS platform (generates ios/ directory):
//        npx cap add ios
//
//   3. Sync config to iOS project:
//        npx cap sync
//
//   4. Add microphone permission to ios/App/App/Info.plist:
//        <key>NSMicrophoneUsageDescription</key>
//        <string>Myra Learns uses the microphone so you can practice saying
//        words out loud. Your voice is checked for pronunciation and not stored.</string>
//
//   5. Open in Xcode:
//        npx cap open ios
//
//   6. In Xcode:
//        - Set Signing Team (your Apple Developer account)
//        - Set Deployment Target to iOS 14.3
//        - Add app icons in Assets.xcassets → AppIcon (need 1024×1024 PNG source)
//        - Product → Archive → Distribute to App Store
//
// See tasks/iphone-analysis.md for the complete publishing guide.
// ─────────────────────────────────────────────────────────────────────────────
