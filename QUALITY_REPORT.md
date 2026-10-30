# QA report — CampusRide Admin Cloud Edition

- Local Python automated tests: **7 passed** (`backend/tests`).
- Frontend JavaScript `node --check`: **passed**.
- Browser component QA using an isolated in-memory API-response harness: **Today, Buses, Routes, Drivers, Settings, and Bus Detail + CSV link all rendered**, zero JavaScript runtime errors; output `CAMPUSRIDE_ADMIN_CLOUD_PREVIEW.png` is a QA fixture screenshot with fictional demo records, not a live cloud deployment.
- Tests cover duplicate boarding prevention, student/driver role authorization, route allocations, fee ledger, CSV output, sample-trip isolation, conditional GPS auto-completion, PostgreSQL schema translation, placeholder rewrite/insert identifiers and Vercel proxy configuration.
- **Unverified:** Live database migrations against Neon, actual Render deployment, actual Vercel proxy/cookie behavior and physical mobile GPS/APK. An isolated cloud staging deployment is required before production use.
- The Vercel proxy URL in `frontend/vercel.json` is initially a placeholder, and must be configured using `scripts/configure_vercel.py` before deploying.
- No Neon, Render, or Vercel accounts were accessed and no user credentials were requested or embedded.
