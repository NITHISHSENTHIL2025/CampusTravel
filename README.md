# CAMPUSRIDE — Admin Cloud Edition (Vercel + Render + Neon)

**Delivered now:** deployable **admin-first** source, with one shared FastAPI API and PostgreSQL data model prepared for the later Driver and Student Android APKs. No `.bat` launcher. Admin interface: **Today · Buses · Routes · Drivers · Settings** in black/white liquid UI.

## Read this first

- **This is a source release, not a deployed service.** You must create/connect your own Vercel, Render and Neon projects. The cloud integration has not been run against your private services and requires an end-to-end deployment test.
- **No compiled Android APK is claimed** in this admin-first release. Driver/Student role APIs remain present so that the Android apps can be connected after the admin site works.
- **Never put Neon `DATABASE_URL`, student passwords, or admin secrets in Vercel/public code.** Set them only in Render's secret environment.
- **No actual university roster** is supplied. Live startup creates an empty database. Optional `seed_demo.py` creates expressly fictional test users, routes, fee balances and *test* trips in a separate database/branch; it does not create real `@klh.edu.in` identities.
- **No paid service is required just to try the architecture**, subject to providers' current free-plan limitations. Render free services may sleep when idle.

## Project layout

```
frontend/                     Vercel static UI: login, admin and mobile web views
  index.html
  vercel.json                /api path proxy to Render (configure before deploy)
  assets/                    original source UI, not remote library CDN
backend/                      Render FastAPI API
  app/main.py                authentication, roles, fixed allocations, routes, trips and GPS
  app/store.py               Neon PostgreSQL adapter; local SQLite only for tests
  app/forecast.py            withholds predictions until enough verified history exists
  seed_demo.py               opt-in completely fictional records; prints private local credentials path
  manage.py                  admin setup, retention cleanup, opt-in seed
  requirements.txt
  tests/
render.yaml                   optional Render Blueprint
scripts/configure_vercel.py   safe script to connect your Vercel API rewrite
DEPLOY_CHECKLIST.md           exact cloud setup, 3-device test, security checks
```

## Do this in order

1. **Neon:** create a fresh PostgreSQL database or isolated demo branch. Copy the **pooled connection string** with `sslmode=require`.
2. **GitHub:** upload the extracted project as a new private repository. Never commit a `.env` file or `DEMO_CREDENTIALS_PRIVATE.txt`.
3. **Render:** New Web Service, repository root directory `backend`, build `pip install -r requirements.txt`, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers`. Enter the Render environment variables described in `DEPLOY_CHECKLIST.md`.
4. **Vercel:** once Render supplies its HTTPS URL, configure `frontend/vercel.json` with `python scripts/configure_vercel.py https://YOUR-RENDER-APP.onrender.com` and push the change. Create a Vercel project using **Root Directory = `frontend`** and **Framework Preset = Other**; no build command is required. The output is `.`.
5. **Connect origins:** set `CAMPUSRIDE_WEB_ORIGIN` on Render to your exact Vercel domain (for example `https://campusride-admin.vercel.app`) then redeploy/restart. All browser API calls are **same-origin `/api/…`** via Vercel's reverse proxy, retaining the HttpOnly session cookie and CSRF header.
6. **Sign in** at the Vercel URL. If you configured bootstrap credentials, change the password and remove the bootstrap password environment variable on Render.
7. **Create two buses and routes** through admin, or run opt-in `seed_demo.py` on a clean, separate Neon demo branch using your private Neon URL (instructions below).
8. **Test** five admin pages, bus detail → roster CSV, route stops, driver assignment, today trip statuses, then an authenticated driver GPS submission.

## Optional demo seed — only for a separate clean test database

On your own computer (PowerShell, with Python installed), in the unzipped project folder:

```powershell
cd backend
python -m pip install -r requirements.txt
$env:DATABASE_URL = 'YOUR_NEON_POOL_CONNECTION_STRING'
$env:CAMPUSRIDE_REQUIRE_POSTGRES = '1'
python manage.py seed-demo
```

**This generates 2 fictional buses, 2 fake route circuits with points near the verified campus area, 24 fake students, 2 fake drivers and 8 marked TEST shifts (today + tomorrow).** The script refuses to run when an existing fleet/route/student is present. Unique random temporary credentials are written to a *local-only*, ignored file `backend/DEMO_CREDENTIALS_PRIVATE.txt`. Keep it private and delete it after demonstration. The text in demo data never claims to be genuine KLH students, bus locations or fee records. Set `CAMPUSRIDE_DEMO=1` on your Render service when presenting this demonstration database.

## Local backend test (no Windows batch)

```bash
cd backend
python -m pip install -r requirements.txt
python -m pytest -q
# You may run locally with SQLite ONLY for disposable local testing:
uvicorn app.main:app --reload
```

A local SQLite server does **not** prove that cloud Neon is connected. Cloud deployment is configured to **fail closed** if `DATABASE_URL` is missing.

## Actual features

- Today: bus schedule and morning/evening statuses, genuine GPS feed, started/reached times saved on trips, date selection. An old or unavailable position is not displayed as live.
- Buses: registration plate, capacity, assigned route, assigned driver, student count, popup student roster and CSV export.
- Routes: ordered stops, round-trip reference, map, assigned bus/driver, roster CSV, assignment workflow.
- Drivers: individual driver accounts, vehicle/route assignment popup and contact information when entered.
- Settings: real student and driver account creation, fixed student-to-route pickup allocations, fee ledger, scheduled shifts, notices, admin password and activity administration.
- API: permission-scoped student/driver mobile experiences ready for APK integration, QR boarding and duplicate protection, GPS, incidents, audit and test-trip exclusion from true ridership analytics.

## Limitations and safety

- Map route polylines connect stored stop coordinates, **not an actual road routing engine**. Validate real stops and the actual bus gate before deployment to students.
- A live GPS location only appears after an authenticated driver sends a device position; the admin UI does not invent live coordinates.
- Return-to-campus auto-finish is an experimental GPS geofence feature with manual override, not a safety-certified bus-arrival system.
- Location/fee/student data is sensitive. A full university launch requires consent, data retention, privacy approval, access reviews, backup/recovery, operational monitoring, staging, load and penetration tests.
- No live Neon/Render/Vercel integration or physical Android tracking test was possible in this build environment. Review `DEPLOY_CHECKLIST.md` before claiming campus production readiness.
