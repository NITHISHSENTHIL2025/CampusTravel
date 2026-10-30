# CampusRide Student — native Android source

This is an **Android Studio project**, not a precompiled APK. Android Gradle Plugin 8.8.2, SDK 35 and JDK 17+ are required.

1. Open this folder in Android Studio (open the root containing `settings.gradle`).
2. Let Gradle synchronize; install the Android SDK 35 packages if requested.
3. Use **Build > Build Bundle(s) / APK(s) > Build APK(s)** to create the APK. No APK is claimed or provided without compiling and signing.
4. Configure the same **HTTPS CampusRide server URL** when the app launches; HTTP LAN URLs will not work for browser GPS.
5. Sign in with a matching student account created by the backend.

Security: this WebView accepts HTTPS only and has no arbitrary external URL navigation. The Android driver application implements an actual LocationManager foreground service with a persistent GPS notification, native location permission and authenticated server submissions. The foreground service starts only while the activity is visible. Driver must stop tracking at trip completion. Battery optimizations and location permissions can affect updates. Test on a physical Android device; this source has not been compiled in the current environment (no Android SDK).

University deployment requires formal authorization, app signing, pen-test, data privacy review and reliable HTTPS hosting. Do not use the demo credentials in production.
