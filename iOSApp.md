# iOS / Mobile App Notes

## Current Mobile Friendliness: Decent but Incomplete

**What's already there:**
- `<meta name="viewport">` tag
- One media query at `@media (max-width: 700px)` that stacks dino + lesson columns vertically and scales fonts/buttons down
- Flexbox layout that reflows correctly
- Reasonable button sizes for touch

**What's missing or problematic:**
- **iOS audio quirks**: iOS Safari requires a user gesture before any audio plays, and has a broken/missing MediaRecorder API in older versions — the "Hear It!" / "Say It!" flow may fail silently on iPhones
- **No PWA manifest** — can't be installed to home screen
- Speech bubble uses absolute positioning that can clip awkwardly on very narrow screens (< 360px)
- No `touch-action` or `-webkit-tap-highlight-color` polish
- Debug panel doesn't limit width on small screens and can cause horizontal scroll

---

## Making it a True Mobile App

| Approach | Effort | Notes |
|----------|--------|-------|
| **PWA** (Progressive Web App) | Low | Add `manifest.json` + service worker. "Add to Home Screen" works on Android; iOS partial support. Still a browser app. |
| **Capacitor** (wrap existing HTML/JS in native shell) | Medium | Keeps all current HTML/CSS/JS. Uses native microphone API instead of browser's broken iOS one. Produces real `.ipa` / `.apk`. |
| **React Native / Flutter rewrite** | High | Full rewrite of frontend. Best performance/UX but abandons existing code. |

## The Real Bottleneck

The app requires a running FastAPI server. For a mobile app, need to either:
1. Host the backend remotely (VPS or cloud), or
2. Bundle Whisper inference on-device (very heavy, ~500MB+)

## Recommended Path

1. **PWA first** — low effort, fixes "Add to Home Screen" UX
2. **Fix iOS microphone/audio issues** in `static/js/app.js`
3. **Capacitor** if App Store distribution is needed