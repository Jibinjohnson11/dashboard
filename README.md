# SEO Management Dashboard

A role-based dashboard for an SEO agency to manage **brands, campaigns, team members, content writing, client approvals and published backlinks**.

- **Backend:** Python 3.12+, Django 6.0 (views, forms, auth, sessions, CSRF)
- **Business data:** MongoDB (via PyMongo), compatible with MongoDB Atlas
- **Auth / sessions:** Django's relational database — SQLite locally, PostgreSQL in production
- **Frontend:** Django templates, plain HTML/CSS, vanilla JavaScript, Chart.js (vendored), inline Lucide icons. No frontend framework.

---

## Contents

1. [Features](#features)
2. [Roles and permissions](#roles-and-permissions)
3. [Architecture](#architecture)
4. [Data model (MongoDB)](#data-model-mongodb)
5. [Local setup](#local-setup) (macOS, Linux, Windows)
6. [Configuration (.env)](#configuration-env)
7. [Running the tests](#running-the-tests)
8. [Demo data](#demo-data)
9. [Deployment](#deployment)
10. [Security notes](#security-notes)
11. [Troubleshooting](#troubleshooting)
12. [Known limitations](#known-limitations)

---

## Features

| Module | What it does |
| --- | --- |
| **Dashboard** | Eight live summary cards, a content-status chart, a 30-day publishing chart, campaign progress, and recent activity. Everything is filtered to what the signed-in user may see. |
| **Brands** | Create, view, edit, archive and restore brands. Brand pages show their campaigns, assigned team and content totals. |
| **Campaigns** | Campaigns always belong to one brand. They hold primary and secondary keywords (click to copy), a target URL (copy button), dates, status and assigned writers. You can search and filter by brand or status. |
| **Team** | Invite members by email (they set their own password), approve public sign-ups, change roles, assign brands and campaigns, deactivate accounts, and view work history. |
| **Content Writing** | List and filter content. The editor only opens from **Create New Content** or **Edit**. It has a rich-text editor (headings, bold, italic, underline, lists, quotes, links), a word count, **Save Draft** and **Submit for Approval**, and full revision history. |
| **Content Approvals** | Reviewers, admins and clients see submitted content and can **Approve**, **Request Changes** (feedback required) or **Reject** (reason required). The full approval history is kept. |
| **Published Content** | Record where approved content went live (URL, publisher, date, notes). You can search and filter by brand, campaign and date, copy or open URLs, edit details (audited), and export to CSV. |
| **Settings** | Profile, password change, and (for admins) system status. |

### Content lifecycle

```
draft ──submit──▶ pending ──approve──▶ approved ──record publication──▶ published
  ▲                 │  │                  │
  │                 │  └──reject──▶ rejected   (final; can never be published)
  │                 └──request changes──▶ needs_edit ──revise & resubmit──▶ pending
  └──────────────── revise (creates a new version, needs a fresh review) ◀──┘
```

- Submitting **locks** a version. Any later edit creates version *n + 1*, so each review decision refers to one version that can't change.
- Writers can edit `draft` and `needs_edit` content only. Content that is submitted, approved, rejected or published is read-only to them.
- To change approved content you start a **revision**, which goes back through review.
- A publication records the exact approved version that was published. Each content item can be published once.

---

## Roles and permissions

| Module | Admin | Team Member | Reviewer | Client |
| --- | :-: | :-: | :-: | :-: |
| Dashboard | ✅ | ✅ (own scope) | ✅ (own scope) | ✅ (own brands) |
| Brands | ✅ manage | view assigned | view assigned | view own |
| Campaigns | ✅ manage | view assigned | view (assigned brands) | — |
| Team | ✅ | — | — | — |
| Content Writing | ✅ | write/edit own drafts | — | — |
| Content Approvals | ✅ (not own work) | — | ✅ assigned brands | ✅ own brands |
| Published Content | ✅ record/edit | record/edit own | view | view own brands |
| Settings | ✅ | ✅ | ✅ | ✅ |

**How it's enforced:**

- **Deny by default.** Django's `LoginRequiredMiddleware` protects every view. A user without an *active* MongoDB team profile and a valid role can reach only the "account pending" page.
- **Module access.** `@module_required("<module>")` checks the role (`core/permissions.py`).
- **Object-level access.** Every MongoDB query is combined with a per-role scope (`brand_scope`, `campaign_scope`, `content_scope`, `publication_scope`). A record outside your scope returns **404**, not 403, so other clients' IDs can't be probed.
- **Business rules live in services.** Examples: no self-approval, feedback required, approved-only publishing, and no self-demotion for admins. These run on the server; hiding a button is only a convenience.
- **Clients** see only brands they are explicitly assigned to (`brand_access`). They never see drafts, internal notes or other clients.

---

## Architecture

```
config/          settings, URLs, WSGI/ASGI
core/            mongo.py (client + indexes), repository.py (base data access),
                 permissions.py (roles, principal, scopes, decorators), middleware.py,
                 sanitize.py (nh3 allowlist), ratelimit.py, audit.py, logging.py,
                 templatetags/ui.py, management commands (init_mongodb, seed_demo_data)
accounts/        custom email-based User model, login/register/reset/invite views, forms
dashboard/       dashboard stats & charts, global search, /healthz/
brands/ campaigns/ teams/ content/ approvals/ publications/
                 each = repositories.py (MongoDB queries) → services.py (business rules)
                        → forms.py (validation) → views.py (thin) → urls.py, tests.py
templates/       base.html, layouts/, includes/ (sidebar, topbar, pagination, …), one folder per module
static/          css/{base,forms,dashboard,responsive}.css, js/{main,content,campaigns,teams,charts}.js,
                 vendor/chart.umd.min.js (Chart.js 4.4.1)
scripts/         init_mongodb.py, seed_demo_data.py (wrappers around the management commands)
```

**Layering rule:** views call services, services call repositories, and only repositories touch PyMongo collections. Each request gets a `request.principal` (the Django user plus their MongoDB team profile), and that principal is passed to every service call.

### Two databases, no distributed transaction

Django auth lives in SQL and business data lives in MongoDB, so there is no atomic transaction across the two. The code handles partial failures like this:

- **Creating a user** (registration or invite): the Django user is created first, then the MongoDB profile. If the MongoDB write fails, the Django user is deleted (compensation).
- **A Django user with no profile** (for example after a crash) is treated as *pending* and denied access. Admins can repair it from the Team page.
- **State changes** use a single `find_one_and_update` whose filter includes the expected state (status, version and an `edit_seq` counter). Two reviewers clicking at once, or a stale browser tab, get a clear conflict message and nothing is silently overwritten.
- **Unique indexes** back the business rules: one decision per submitted version, one publication per content item, unique brand names, and unique campaign names per brand.
- **Publishing** first moves the content from `approved` to `published` atomically, then inserts the publication. If the insert fails, the status change is rolled back.

---

## Data model (MongoDB)

| Collection | Key fields | Indexes |
| --- | --- | --- |
| `brands` | name, name_lower, website, industry, contact_*, description, status, archived_at | unique `name_lower`; `status+created_at` |
| `campaigns` | brand_id → brands, name, primary_keyword, secondary_keywords[], target_url, target_location, start/end_date, assigned_user_ids[], status | unique `brand_id+name_lower`; `status`; `assigned_user_ids` |
| `team_profiles` | django_user_id → auth user, display_name, email, role, status | unique `django_user_id`; `role+status` |
| `brand_access` | django_user_id, brand_id, access_role | unique `django_user_id+brand_id`; `brand_id` |
| `campaign_assignments` | campaign_id, django_user_id | unique pair; `django_user_id` |
| `contents` | brand_id, campaign_id, title, keyword, content_type, status, current_version, edit_seq, writer_user_id, submitted_version, approved_version(_id), timestamps | `brand_id+status`, `campaign_id+status`, `writer_user_id+status`, `updated_at`, `submitted_at` |
| `content_versions` | content_id, version_number, body_html (sanitised), internal_notes, word_count, locked, created_by_user_id | unique `content_id+version_number` |
| `content_approvals` | content_id, content_version_id, version_number, reviewer_user_id, reviewer_role, action, feedback, reviewed_at | **unique `content_version_id`** (one decision per version) |
| `publications` | content_id, approved_version_id, approved_version, brand_id, campaign_id, title/keyword/type snapshot, published_url, published_by_user_id, recorded_by_user_id, published_at, notes | **unique `content_id`**; brand/campaign/date |
| `audit_logs` | actor_user_id, action, entity_type, entity_id, metadata, created_at | `created_at`, `entity_type+entity_id`, `actor_user_id` |

All timestamps are stored as UTC (`tz_aware=True` client). Run `python manage.py init_mongodb` to create every collection and index. It's safe to run again at any time.

---

## Local setup

The app runs at **http://127.0.0.1:8000/**.

### 1. Install Python 3.12 or newer

- **macOS:** `brew install python@3.12` or use the installer from python.org
- **Linux (Debian/Ubuntu):** `sudo apt install python3.12 python3.12-venv`
- **Windows:** install from python.org and tick **"Add python.exe to PATH"**

### 2. MongoDB: local or Atlas

**Option A: local MongoDB Community Server**

- **macOS:** `brew tap mongodb/brew && brew install mongodb-community && brew services start mongodb-community`
- **Linux:** follow MongoDB's install guide for your distro, then `sudo systemctl start mongod`
- **Windows:** install the MSI from mongodb.com. It runs as the "MongoDB" service.
- **Docker (any OS):** `docker run -d --name seo-mongo -p 27017:27017 mongo:8`

Then use `MONGODB_URI=mongodb://127.0.0.1:27017` in `.env`.

**Option B: MongoDB Atlas (free tier)**

1. Create a cluster, then a database user with a strong password.
2. Under *Network Access*, add your IP address.
3. Copy the connection string, for example `mongodb+srv://USER:PASSWORD@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority`, into `MONGODB_URI`.

**Option C: no MongoDB yet (quick look only).** Set `MONGODB_USE_MOCK=true` to use an in-memory database. Data is lost on every restart, and production refuses to start with this setting.

### 3. Create a virtual environment and install dependencies

macOS / Linux:

```bash
cd seo-management-dashboard      # the folder containing manage.py
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows (PowerShell):

```powershell
cd seo-management-dashboard
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1        # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
```

### 4. Configure `.env`

```bash
cp .env.example .env              # Windows: copy .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(50))"   # paste the output into DJANGO_SECRET_KEY
```

Edit `MONGODB_URI` and the other values as needed. See [Configuration](#configuration-env).

### 5. Initialise the databases

```bash
python manage.py migrate          # Django auth/session tables (SQLite: db.sqlite3)
python manage.py init_mongodb     # MongoDB collections + indexes (or: python scripts/init_mongodb.py)
```

### 6. Create the first admin

```bash
python manage.py create_admin --email you@agency.com --name "Your Name"
```

You'll be asked for a password, which must pass the same strength rules as everyone else's. Django's `createsuperuser` also works: a superuser becomes an admin profile the first time they sign in.

### 7. Start the server

```bash
python manage.py runserver
```

Open **http://127.0.0.1:8000/** and sign in.

You only need `python manage.py collectstatic` for production. In development, static files are served straight from `static/`.

### 8. Try it out

1. **Registration:** sign out, click *Create an account* and register. The account is created as **pending**. Sign in as the admin and open *Team → "N new accounts waiting"*, then choose a role and **Activate account**.
2. **Invitations:** *Team → Add Team Member*. With the default console email backend, the invitation link is **printed in the terminal** running `runserver`. Open it to set a password.
3. **Password reset:** *Forgot password?* on the login page. The reset link is also printed in the terminal.
4. **Workflow:** as admin, create a brand, then a campaign with a team member assigned. As that team member, choose *Create New Content* and submit it. As a reviewer or client assigned to the brand, request changes or approve it. As the writer, record the publication.

---

## Configuration (.env)

| Variable | Default | Purpose |
| --- | --- | --- |
| `DJANGO_ENV` | `development` | `production` turns on HTTPS-only cookies, HSTS, SSL redirect and hashed static files, and forces `DEBUG` off |
| `DJANGO_DEBUG` | `true` in dev | Ignored (always off) in production |
| `DJANGO_SECRET_KEY` | random per run in dev | **Required in production** |
| `DJANGO_ALLOWED_HOSTS` | `127.0.0.1,localhost` | Comma-separated |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | — | For example `https://seo.example.com` |
| `DATABASE_URL` | SQLite `db.sqlite3` | Production: `postgres://user:pass@host:5432/db` |
| `MONGODB_URI` | `mongodb://127.0.0.1:27017` | Local or Atlas URI |
| `MONGODB_DB_NAME` | `seo_dashboard` | |
| `MONGODB_USE_MOCK` | `false` | In-memory MongoDB for a quick look (not allowed in production) |
| `REGISTRATION_MODE` | `open_pending` | `open_pending` (public sign-up, admin approves) or `invite_only` |
| `EMAIL_BACKEND` | console | Production: `django.core.mail.backends.smtp.EmailBackend` |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`/`EMAIL_USE_SSL`, `DEFAULT_FROM_EMAIL` | | SMTP settings |
| `SESSION_COOKIE_AGE_SECONDS` | 28800 (8 h) | Absolute session lifetime when "Remember me" is unticked. The cookie also expires when the browser closes. |
| `REMEMBER_ME_AGE_SECONDS` | 1209600 (14 d) | |
| `PASSWORD_RESET_TIMEOUT_SECONDS` | 3600 | Reset link lifetime |
| `INVITE_TOKEN_TIMEOUT_SECONDS` | 259200 | Invitation link lifetime (72 h) |
| `LOGIN_RATE_LIMIT` / `LOGIN_RATE_WINDOW_SECONDS` | 5 / 900 | Failed logins allowed per email (the per-IP limit is 4×) |
| `PASSWORD_RESET_RATE_LIMIT` / `..._WINDOW_SECONDS` | 5 / 3600 | Reset requests per email (the per-IP limit is 2×) |
| `CACHE_BACKEND` | `locmem` | Use `db` in production so rate limits are shared across workers (`python manage.py createcachetable`) |
| `DJANGO_BEHIND_PROXY` | `false` | Trust `X-Forwarded-Proto` from your TLS-terminating proxy |
| `DJANGO_TRUST_X_FORWARDED_FOR` | `false` | Use the first `X-Forwarded-For` IP for rate limiting. Only enable this behind a proxy you control. |
| `LOG_LEVEL` | `INFO` | Logs are JSON lines on stdout |

---

## Running the tests

```bash
python manage.py test
```

The suite contains **93 tests**. They always run against an **isolated in-memory MongoDB** (forced by settings when the command is `test`; `MongoTestCase` asserts it) and a throwaway SQL test database, so they never touch real data.

Coverage:

| Area | Tests |
| --- | --- |
| Authentication | registration (pending, normalised email, hashed password, no role selection), duplicate email, weak passwords, invite-only mode, login/logout (POST-only), remember-me, generic errors, safe `next` redirect, login rate limiting, deactivated users are signed out, full password-reset flow, single-use and expired tokens, reset rate limiting, invitation and account activation |
| Permissions | full role × module matrix, sidebar filtering, pending and missing profiles, superuser bootstrap, cross-brand isolation for clients, reviewers and writers (detail pages, lists, search, CSV export), privilege escalation (roles, brands, campaigns, self-demotion, editing others' drafts) |
| Brands and campaigns | CRUD, archive and restore, duplicate names, validation, search and pagination, related data, brand locked on campaign edit, invalid or archived brand references, only active team members can be assigned, date validation, filters |
| Content workflow | save draft, submit (read-only afterwards), empty-submit blocked, approve, client approval, request changes, revise, resubmit (new version), reject (blocks publishing), no self-approval, team members cannot review, duplicate approval, outdated revision, **concurrent approvals (threaded: exactly one wins)**, stale edits, approved content locked, revision flow |
| Publishing | approved only, records the approved version, duplicate content and URL, future dates, unsafe URLs, reviewer and client blocked, audited edits, filters, CSV export, dashboard counts |
| Security | CSRF enforcement, HTML sanitisation (script, event handlers, `javascript:`, `style`, iframes), sanitised storage, invalid and malicious ObjectIds give 404, regex-injection-safe search, output escaping, security headers, CSV formula injection, health check |

---

## Demo data

**For development only.** The command refuses to run unless `DJANGO_ENV=development` and `DJANGO_DEBUG=true`.

```bash
python manage.py seed_demo_data                 # random passwords, printed once
python manage.py seed_demo_data --reset         # wipe MongoDB business data + demo users first
python manage.py seed_demo_data --password "Your-Dev-Passw0rd"   # same password for all demo users
```

It creates 3 brands, 5 campaigns, 1 admin and 4 team members (2 writers, 1 reviewer with access to all brands, and 1 client with access to *Lumen Analytics* only), and 25 content items: 10 drafts, 5 pending, 5 approved and 5 published. Everything goes through the real services, so all relationships are valid. Demo users use `@demo.example` addresses.

---

## Deployment

The app is ready for any Django-compatible host (Render, Railway, Fly.io, Heroku-style platforms, a VM, or Docker). **Nothing has been deployed.** Deploying needs your approval and your credentials.

### Checklist

1. **Environment:** start from `.env.production.example` and set real values in your host's secret manager. Never commit them.
   Required: `DJANGO_ENV=production`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `DATABASE_URL` (PostgreSQL), `MONGODB_URI` (Atlas) and SMTP.
2. **HTTPS:** terminate TLS at your host or load balancer. Set `DJANGO_BEHIND_PROXY=true` if it sets `X-Forwarded-Proto`. Production turns on secure cookies, HSTS (1 year) and SSL redirect. `/healthz/` is exempt from the redirect.
3. **Release commands** (the `Procfile` `release:` line does this):
   ```bash
   python manage.py migrate --noinput
   python manage.py createcachetable      # because CACHE_BACKEND=db
   python manage.py init_mongodb
   python manage.py collectstatic --noinput   # the Dockerfile runs this at build time
   python manage.py create_admin --email ... --name ...   # once
   ```
4. **Web process:** `gunicorn config.wsgi:application -c gunicorn.conf.py`. It binds to `$PORT`, and worker and thread counts can be changed with `GUNICORN_*` variables. WhiteNoise serves static files with hashed names and long-lived caching.
5. **Health check:** `GET /healthz/` returns `200 {"status":"ok"}` when both SQL and MongoDB are reachable, and `503` otherwise.
6. **Check settings:** `DJANGO_ENV=production ... python manage.py check --deploy`. The only remaining warning is HSTS preload, which is deliberately opt-in through `DJANGO_HSTS_PRELOAD` because it is hard to undo.
7. **Email:** use your provider's SMTP credentials (SendGrid, SES, Postmark, Mailgun and so on). Set `REGISTRATION_MODE=invite_only` if you don't want public sign-ups.

**Docker:**

```bash
docker build -t seo-dashboard .
docker run --env-file .env.production -p 8000:8000 seo-dashboard
```

### MongoDB Atlas in production

- Use a dedicated database user with `readWrite` on your database only.
- Restrict *Network Access* to your host's egress IPs (or use VPC peering or Private Link).
- Use an M10+ cluster if you need continuous backups.

### Backups

- **MongoDB:** Atlas Cloud Backup with point-in-time restore, or `mongodump --uri "$MONGODB_URI" --out backup-$(date +%F)` on a schedule, with copies stored off-site.
- **PostgreSQL:** your provider's automated backups, or `pg_dump "$DATABASE_URL" > auth-$(date +%F).sql`.
- Back up **both** databases at the same time. Content refers to users by Django user ID.
- Test a restore regularly.

---

## Security notes

- Passwords are hashed with Django's PBKDF2 and checked against strength validators: 10 or more characters, a letter plus a number or symbol, not a common password, and not similar to the user's details. They are never emailed or logged.
- Login and password-reset are rate limited per email and per IP. Error messages are generic, and the reset page never reveals whether an email exists. Registration doesn't reveal it either.
- Password-reset and invitation tokens use Django's signed, time-limited token generator, with separate salts. They become invalid once used, after a login, or after a password change. Tokens are never logged.
- CSRF protection is on for every form. Logout is POST-only. After login, users are only redirected to same-host `next` URLs.
- Rich text is sanitised on the server with **nh3** (Rust `ammonia`) using an allowlist. The allowed tags are p, br, h2–h4, strong/b, em/i, u, ul/ol/li, a and blockquote. Links may only use http, https or mailto and get `rel="noopener noreferrer nofollow"`. Content is sanitised again on output.
- Strict **Content-Security-Policy** (`script-src 'self'`, no inline scripts or styles, `frame-ancestors 'none'`), plus `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Cross-Origin-Opener-Policy`, `Permissions-Policy`, and `Cache-Control: no-store` on authenticated pages.
- Search input is regex-escaped, and filter IDs are validated as ObjectIds, so no query or regex injection is possible.
- CSV export neutralises spreadsheet formulas (`=`, `+`, `-`, `@`).
- Audit logs record who did what (logins, every workflow transition, publication edits, role and status changes). Sensitive fields are stripped.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `ServerSelectionTimeoutError` / "Cannot reach MongoDB" | Check MongoDB is running (`brew services list`, `systemctl status mongod`, or the Windows Services app). Check `MONGODB_URI`. On Atlas, add your IP under *Network Access* and URL-encode special characters in the password. |
| `mongodb+srv` DNS errors | `dnspython` is installed with PyMongo. Check that your network allows SRV DNS lookups, or use the standard (non-srv) connection string from Atlas. |
| `/healthz/` returns 503 | The JSON response shows which database (`database` or `mongodb`) is down. |
| `no such table: accounts_user` | Run `python manage.py migrate`. |
| Migration conflicts after pulling | `python manage.py showmigrations`, then `python manage.py migrate`. Never edit an applied migration. |
| `ImproperlyConfigured: DJANGO_SECRET_KEY must be set` | You're running with `DJANGO_ENV=production`. Set a secret key, or use `development` locally. |
| No password-reset or invite email | In development, emails print to the **runserver terminal** (console backend). For SMTP, check `EMAIL_HOST`, `EMAIL_PORT` and TLS/SSL (port 587 = `EMAIL_USE_TLS=true`, 465 = `EMAIL_USE_SSL=true`, never both). Many providers also require a verified sender in `DEFAULT_FROM_EMAIL`. Only *active* users with a password get reset emails. Invited users need an invitation link, which an admin can resend. |
| SMTP `SMTPAuthenticationError` | Use an app password or API key (Gmail and Microsoft 365 block basic passwords). |
| "Invalid email or password, or the account is not active yet" for a correct password | The account is pending or deactivated. An admin must activate it under *Team*. |
| "Too many failed sign-in attempts" | Wait for the window (15 min by default), or restart the dev server (the in-memory cache clears). |
| Signed in but sent to "Your account isn't active yet" | The MongoDB profile is pending, inactive, or missing a role. An admin can fix it under *Team → Edit & assign*. |
| CSRF verification failed (403) in production | Add your full origin, including `https://`, to `DJANGO_CSRF_TRUSTED_ORIGINS`. Behind a proxy, also set `DJANGO_BEHIND_PROXY=true`. |
| Styles missing in production | Run `collectstatic` (the Dockerfile does) and confirm `DJANGO_ENV=production` so WhiteNoise's manifest storage is used. |
| Windows: `Activate.ps1 cannot be loaded` | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once. |

---

## Known limitations

- **Cross-database atomicity:** SQL and MongoDB can't share a transaction. The app uses compensation and precondition-guarded updates instead (see [Architecture](#two-databases-no-distributed-transaction)). A crash between two steps can leave an orphaned row. It is treated as "deny access" and can be repaired from the UI.
- **Rate limiting** uses Django's cache. With the default `locmem` cache, each process has its own counters, so use `CACHE_BACKEND=db` (or Redis) when running several workers.
- **The rich-text editor** is a small built-in editor based on `contenteditable` and `document.execCommand`. That browser API is deprecated but still supported in all major browsers. All of its output is sanitised on the server, so it isn't a security risk. Pasted content is inserted as plain text on purpose.
- **Notifications** are in-app only (top-bar badge). No email is sent when a review decision is made.
- **Publishing is record-keeping only.** The app doesn't post to external sites or check that a URL is live.
- Each content item can have **one** publication record. Content that is syndicated to several URLs needs one content item per URL.
