# CampusRide Admin — deployment and verification checklist

## A. Neon

1. Create a **new demo project or branch** in Neon (https://console.neon.tech/). Do not use an existing application database.
2. Open the project **Connection Details** panel, enable **Pooled connection**, and copy the connection string with `sslmode=require`.
3. Keep the URL confidential. It belongs **only** in Render `DATABASE_URL` and your private local terminal if running the opt-in demo seed.
4. Database tables are created at FastAPI startup by `app.store.init_db()` if `DATABASE_URL` is set. An existing database is not automatically erased.

## B. Render Web Service

**Choose repo Root Directory = `backend`** (or use the included `render.yaml` Blueprint).

- Runtime: Python 3
- Build: `pip install -r requirements.txt`
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers`
- Health check: `/api/health`

**Environment variables:**

| Name | Value |
|---|---|
| `DATABASE_URL` | Exact secret Neon pooled PostgreSQL URL |
| `CAMPUSRIDE_REQUIRE_POSTGRES` | `1` |
| `CAMPUSRIDE_SECURE_COOKIES` | `1` |
| `CAMPUSRIDE_TIMEZONE` | `Asia/Kolkata` |
| `CAMPUSRIDE_DEMO` | `1` for wholly fake demo branch, otherwise `0` |
| `CAMPUSRIDE_WEB_ORIGIN` | Exact Vercel HTTPS site origin; update after first Vercel deploy |
| `CAMPUSRIDE_BOOTSTRAP_NAME` | Your administrator name, for initial setup only |
| `CAMPUSRIDE_BOOTSTRAP_EMAIL` | Your private administrator email, for initial setup only |
| `CAMPUSRIDE_BOOTSTRAP_PASSWORD` | Unique long 16+ character password, for initial setup only |

Open `https://YOUR-SERVICE.onrender.com/api/health` and confirm `ok:true` (no need to expose credentials). **Remove bootstrap password** from Render environment after admin account exists and change the initial admin password immediately.

**If Render fails on startup:** check Build Logs for dependency errors; if the traceback says `DATABASE_URL must be configured`, set the Neon secret; if the DB connect fails, confirm Neon host and `sslmode`.

## C. Vercel

1. After Render is running, from the repository root run:
   `python scripts/configure_vercel.py https://YOUR-SERVICE.onrender.com`
2. Commit the changed `frontend/vercel.json` and push to GitHub.
3. Create a new Vercel project from the same repo with **Root Directory `frontend`**, Framework Preset **Other**, Output Directory `.` (or the default root/static directory, if presented), and no build step.
4. Deploy and note `https://YOUR-PROJECT.vercel.app`. Set the exact value in Render `CAMPUSRIDE_WEB_ORIGIN` then redeploy the Render backend.
5. Open `/admin` at your Vercel address. The login and all `/api/` calls must stay on this domain in the browser Network panel; Vercel sends API requests to Render behind the scenes.

Do NOT use a separate frontend `VITE_DATABASE_URL`, `NEXT_PUBLIC_DATABASE_URL`, or expose Neon credentials in JavaScript. Do NOT use wildcard CORS. Set only one trusted Vercel origin for state-changing requests.

## D. Admin smoke tests

1. Admin sign in -> Today; no 403/500 errors in browser Developer Tools.
2. Add bus -> confirm capacity, unique plate validation, route/driver assignment.
3. Add route -> enter real verified or explicitly fictional stops, ordered campus departure and return; popup map renders. **This isn't road-navigation.**
4. Add driver + student accounts -> allocate student to route/stop, verify no duplicate active allocations.
5. Open bus details -> correct student count and downloadable CSV; ensure no students assigned to unrelated routes appear.
6. Create morning/evening shifts -> conflict detection for vehicle and driver.
7. Driver mobile web login at `/driver` -> Start Ride. With actual phone HTTPS GPS enabled, fresh coordinates update Today map; no fake movements.
8. Re-scan same student's QR token -> second boarding must produce HTTP 409.
9. Sign out -> admin endpoints must return HTTP 401.
10. Confirm TEST trips are excluded from genuine analytics.

## E. When to build APKs

Only after Vercel login, Neon persistence and live Render API are verified, compile Driver and Student APKs from the independent Android Studio projects in the previously supplied CampusRide V2 source. They must use the final **HTTPS backend URL** (usually Render, not local `127.0.0.1`). APK compilation and physical GPS behavior remain unverified until tested on actual devices.

## F. Expected limitations

- Free Render may spin down from inactivity, delaying requests and interrupting the driver demo if the service sleeps. Keep it active during a demonstration.
- PostgreSQL adapter is structurally tested; actual Neon SQL execution and migrations must be validated against a clean Neon instance. Do not use an existing production database for the first attempt.
- Demo records are fictional and must never be treated as genuine transport, location, fee, or student information.
