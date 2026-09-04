## Recurring agent charges

Recurring charges run as a one-shot, idempotent process. Do not run this
logic inside Gunicorn or a request handler.

Preview with the production environment and volume:

```bash
railway run python generate_recurring_agent_charges.py --dry-run
```

Execute due charges:

```bash
railway run python generate_recurring_agent_charges.py
```

For automatic execution, configure a separate Railway Cron service with
`python generate_recurring_agent_charges.py` as its command. Each run
processes at most one overdue period per recurring configuration. Repeated
or concurrent executions are safe because every generated movement is
uniquely identified by organization, recurring configuration, and billing
period.

# Deploy staging on Railway (SQLite)

This app is Flask + Gunicorn + SQLite. First staging keeps SQLite with
**one Gunicorn worker** to reduce lock contention. Schema bootstrap runs
as a **release command** (`python init_db.py`), not on every WSGI import.

Local development is unchanged: use `.env` + `python web_app.py` (or
`run_dev.py`). Do not commit `.env`.

## Prerequisites

- GitHub repo with this project (service **Root Directory** =
  `remax_commission_calculator` if the repo is a monorepo).
- Railway account.
- A strong `SECRET_KEY` (never use `dev-secret-key` on staging).

Generate a secret:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## Start and release commands

Configured in this folder:

| File | Role |
|------|------|
| `Procfile` | `web`: Gunicorn with **1 worker** |
| `railway.toml` | `releaseCommand`: `python init_db.py` |
| `wsgi.py` | WSGI entry (`wsgi:app`) |
| `init_db.py` | Creates DB parent dirs, upload dirs, and tables once per deploy |

Gunicorn command:

```bash
gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120
```

`$PORT` is set by Railway. `wsgi.py` runs `create_tables(create_backup=False)`
once at process start so existing SQLite volumes receive column migrations
(e.g. `properties.external_id`) before requests. Full init with backup still
runs via `releaseCommand` (`python init_db.py`).

## Persistent volume (required for SQLite)

Without a volume, redeploys wipe the filesystem and you lose the DB and
uploads.

1. In the Railway service: **Settings → Volumes** (or **Storage**).
2. Mount a volume at `/data`.
3. Point env vars at that mount (see below).

## Environment variables (Railway Variables)

Set these in the Railway dashboard (or `railway variables`). **Do not**
upload a `.env` file.

### Required

| Variable | Staging value |
|----------|----------------|
| `APP_ENV` | `staging` |
| `SECRET_KEY` | strong random hex (required; rejects empty / `dev-secret-key`) |
| `APP_BASE_URL` | public HTTPS URL of the service (e.g. `https://app.jrhone.com`) |
| `APP_BRAND_NAME` | `JRH One` (visible product name) |
| `APP_DOMAIN` | `jrhone.com` (metadata / links) |
| `DATABASE_PATH` | `/data/commission.db` |
| `PRIVATE_UPLOAD_ROOT` | `/data/uploads` |

### Cash AI (Caja v2, optional)

| Variable | Staging value |
|----------|----------------|
| `CASH_AI_PROVIDER` | `openai` (use `mock` only for tests) |
| `CASH_AI_MODEL` | `gpt-4o-mini` (or `gpt-4o` for better vision) |
| `OPENAI_API_KEY` | Railway secret (never commit) |

Receipt images are stored under `PRIVATE_UPLOAD_ROOT/organizations/<org>/cash/receipts/` on the volume.

### Google Calendar (Agenda, optional)

| Variable | Staging value |
|----------|----------------|
| `GOOGLE_CALENDAR_CLIENT_ID` | OAuth client ID (Web application) |
| `GOOGLE_CALENDAR_CLIENT_SECRET` | OAuth client secret (Railway secret) |

In Google Cloud Console, add this **Authorized redirect URI**:

`{APP_BASE_URL}/agenda/calendar/callback`

Example: `https://app.jrhone.com/agenda/calendar/callback`

Each agent connects their own Google account from the agenda chip. Refresh tokens are stored encrypted with `SECRET_KEY`. Without these variables the chip shows “No configurado” and tasks stay in JRH only.

Leave `UPLOAD_DIR` unset for this first staging so logos stay under
`static/uploads` (servable by Flask). Those logo files are **not** on the
volume unless you later persist that path; private docs and SQLite are.

Railway also injects `PORT` automatically.

### Recommended

| Variable | Staging value |
|----------|----------------|
| `FLASK_DEBUG` | `0` |
| `LOG_LEVEL` | `INFO` |
| `SESSION_COOKIE_SECURE` | `1` |
| `SESSION_COOKIE_SAMESITE` | `Lax` |
| `EMAIL_BACKEND` | `resend` or `smtp` (**required** for agent registration emails) |
| `EMAIL_LOGO_URL` | public HTTPS logo URL (optional) |

### Email / Resend HTTP API (required for registration verification)

| Variable | Notes |
|----------|--------|
| `EMAIL_BACKEND` | `resend` (recommended) or `smtp` |
| `RESEND_API_KEY` | Resend API key (`re_...`) — Railway secret |
| `EMAIL_FROM` | Verified sender, e.g. `JRH One <noreply@jrhone.com>` |

Uses HTTPS (`POST https://api.resend.com/emails`) on port 443 — no outbound SMTP required.

Do **not** use `console` or `mock` in staging/production — registration codes will not be delivered.

### SMTP (legacy generic — when `EMAIL_BACKEND=smtp` only)

| Variable | Notes |
|----------|--------|
| `SMTP_HOST` | e.g. `smtp.resend.com` |
| `SMTP_PORT` | e.g. `587` |
| `SMTP_USERNAME` | |
| `SMTP_PASSWORD` | secret |
| `SMTP_USE_TLS` | `1` |
| `EMAIL_FROM` | or `SMTP_FROM` |

## Step-by-step: create and deploy

1. **Push** the branch that contains these deploy files (when you are
   ready; this checklist assumes the code is on GitHub).
2. In Railway: **New Project → Deploy from GitHub repo**.
3. Select the repo. Open the service → **Settings**:
   - **Root Directory**: `remax_commission_calculator`
   - Confirm build uses Nixpacks / detects `requirements.txt`.
4. Attach a **Volume** mounted at `/data`.
5. Open **Variables** and set the table above (at least required vars).
6. Deploy. Railway should:
   - install dependencies from `requirements.txt`;
   - run `python init_db.py` (release);
   - start `Procfile` `web` process (Gunicorn, 1 worker).
7. Open the generated public URL. Set `APP_BASE_URL` to that URL (or your
   custom domain) and redeploy if the URL was not known beforehand.
8. Create the first admin/org as you do locally (CLI helpers or UI), using
   a one-off Railway shell if needed, e.g.:

   ```bash
   railway run python create_organization.py
   railway run python create_admin.py
   ```

   (Exact CLI flags depend on those scripts; run them only after the
   volume and `DATABASE_PATH` point at `/data`.)

## Local development (unchanged)

```bash
# from remax_commission_calculator/
cp .env.example .env   # once; edit secrets locally
python web_app.py
# or: python run_dev.py
```

Optional local schema bootstrap (same script as release):

```bash
python init_db.py
```

## Limitations (SQLite on Railway)

- One worker only for this staging setup (`--workers 1`).
- Do not scale to multiple replicas while on SQLite (each replica would
  get a different DB unless you redesign storage).
- Back up `/data/commission.db` yourself if the data matters.
- Plan a later move to PostgreSQL for real multi-instance production.

## Troubleshooting

- **SECRET_KEY error on boot**: `APP_ENV` is `staging`/`production` but
  `SECRET_KEY` is missing or equals `dev-secret-key`.
- **Empty DB after redeploy**: volume missing or paths not under `/data`.
- **Release failed**: check deploy logs for `init_db.py`; ensure the
  volume is mounted before release runs.
- **Emails with localhost links**: set `APP_BASE_URL` to the public HTTPS
  URL.
