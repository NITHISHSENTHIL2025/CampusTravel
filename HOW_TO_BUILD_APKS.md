# CampusRide Android APK build

Source: driver and student apps, preconfigured to `https://campustravel.onrender.com`. They display separate server-hosted mobile portals. Driver includes a native foreground location service, subject to device permission/validation.

## GitHub Actions
Copy the `android/` and `.github/` directories into your existing CampusTravel repository root and commit/push:

```cmd
git add android .github
git commit -m "Add CampusRide Driver and Student Android APK builds"
git push origin main
```

Go to https://github.com/NITHISHSENTHIL2025/CampusTravel/actions and open the `Build CampusRide Android APKs` run. Both compiled debug APKs appear as downloadable artifacts if the job succeeds.

## Android Studio fallback
Open `android/CampusRideDriver` or `android/CampusRideStudent` directly in Android Studio and select Build > Generate APKs.

## Warnings
- No credential is baked into either app. Use generated local demo driver/student credentials.
- These apps use a secure WebView shell over the existing mobile portals; they are not independent offline-native apps.
- Debug APKs are sideloadable test packages, not a release-signed Play Store product.
- Before presenting GPS, field-test foreground tracking on a real Android phone.
- Driver tracking requires location permissions, an active trip and a live HTTPS backend.
