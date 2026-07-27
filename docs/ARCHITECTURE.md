# Architecture

Scrap360 / Metalería MVP — technical architecture reference. See `BUSINESS_RULES.md` for domain invariants and `DATA_MODEL.md` for the schema.

## Stack

| Layer | Choice |
|---|---|
| Backend | FastAPI 0.124 + Starlette, sync SQLAlchemy 2.0 (no async ORM) |
| Templates | Jinja2, server-rendered; Bootstrap 5.3.3 (CDN) + `app/static/css/style.css` (~3.5k lines custom) |
| DB | SQLite in dev (`./metalleria.db`), Heroku Postgres in prod (`psycopg2-binary`) |
| Migrations | Alembic (46 linear revisions, single head) |
| Auth | Session cookie (`metalleria_session`, Starlette SessionMiddleware, signed with `SECRET_KEY`) |
| Files | Firebase Storage (bucket `metaleria-api-z2h.firebasestorage.app`) |
| PDFs / Excel | Hand-rolled: raw PDF 1.4 writers and SpreadsheetML 2003 XML (`.xls`) — no reportlab/openpyxl |
| Deploy | Heroku app **scrap360** (`https://scrap360-5561d6b8b3c9.herokuapp.com`) |
| Python | 3.12 (`.python-version`); local venv in `.venv/` |

## Layout

```
app/
  main.py           # app factory, session middleware, login/logout, /healthz, navbar badge middleware
  core/             # config (pydantic-settings, .env), security (bcrypt via passlib), datetime_utils (UTC↔local)
  db/               # base.py (DeclarativeBase + model registration), session.py (engine), deps.py (get_db)
  models/           # SQLAlchemy models — see DATA_MODEL.md
  services/         # domain logic + report builders — see BUSINESS_RULES.md
  web/              # THE PRODUCT: admin.py (~16k lines), worker.py, files.py, template_utils.py
  api/              # small JSON API — vestigial, see below
  templates/        # base.html + admin/ (~43 pages) + worker/ (2 pages) + shared note_evidencias.html
  static/           # css/style.css, favicon, uploaded logos (legacy local uploads)
migrations/         # alembic; env.py injects DATABASE_URL from app settings
scripts/            # seeds (super admin, sucursales, materials) — python -m scripts.<name>
development_context/# original client planning PDFs (roles, flows, MVP scope)
secrets/            # Firebase service-account JSON (dev fallback; prod uses FIREBASE_CREDENTIALS_JSON env var)
tests/              # empty
```

## Request flow & layering rules

1. Routes in `app/web/*` do auth/role/branch checks, parse forms, assemble template context, and call services.
2. `app/services/*` own the domain rules and **the transaction**: they call `db.commit()` themselves. `SessionLocal` is `autoflush=False`, so services `db.flush()` explicitly when they need generated ids mid-transaction.
3. `approve_note` and other multi-effect operations pass `commit=False` internally to compose into a single transaction with one commit at the end.
4. There is no context processor; every `TemplateResponse` context passes `request`, `env`, `user` manually.
5. Blurred line to be aware of: report **data assembly** for partner statements, capital, and saldos lives in `app/web/admin.py` (e.g. `_build_partner_statement_report`), while report **rendering** lives in `app/services/*_report_service.py`. This split already caused one bug (a helper defined web-side but called service-side — fixed 2026-07-26, see `KNOWN_ISSUES.md` #1); prefer putting shared query helpers in `note_service`.

## Auth & permissions

- Login: `POST /web/login` → session dict `{id, username, rol, sucursal_id}`. Role/branch are **baked into the cookie**: changing a user's role or deactivating them takes effect only on next login.
- Guards (FastAPI dependencies in `app/web/admin.py:3977-3999`, `worker.py:39`): `require_superadmin`, `require_admin_or_superadmin`, `require_viewer_or_admin_or_superadmin`, `require_worker` (workers only — admins cannot use worker screens).
- Branch scoping: `_get_allowed_sucursal_ids(db, user)` → `None` for super_admin (unrestricted) or a list of branch ids for admins (from the `admin_sucursales` M2M). Applied via `_apply_sucursal_filter`, `_ensure_nota_access`, `_ensure_partner_access`, `_ensure_scrap360_access`.
- `visor` is read-only: routes pass `can_*` capability flags into templates which hide all mutation UI.
- Passwords: bcrypt (passlib), no complexity rules, >72-byte passwords silently truncate. No lockout/rate limiting. Session cookie `https_only=False` (raise in prod when HTTPS confirmed).

## JSON API (`app/api/`) — vestigial

`/api/health`, `/api/materials`, `/api/pricing`, `/api/partners`, `/api/notes` behind a single session check (`app/api/router.py:11-20`) with **no role or branch checks**. No template calls it; it lags the web feature set (can't approve notes, no IVA/cuenta setting); the real product is the web layer. Don't build new features here.

## Files / Firebase Storage (`app/services/firebase_storage.py`)

- Credentials: `FIREBASE_CREDENTIALS_JSON` env var (prod; tolerant parser fixes mangled JSON/YAML and `\n` in private_key) → fallback `FIREBASE_CREDENTIALS_FILE` (`secrets/...json`, dev).
- Object names: `{folder}/{uuid4hex}_{safe_base}{ext}` — never collide, never overwrite.
- **All uploads are `make_public()`** — evidence photos and invoices are publicly readable by (unguessable) URL.
- Size cap `FIREBASE_MAX_MB` (8 MB) enforced at call sites, not in the service.

| Content | Folder |
|---|---|
| Generic evidence (`POST /web/files/upload`) | `evidencias/user_{user_id}` |
| Subpesaje photo | `evidencias/nota_{nota_id}/sub_{subpesaje_id}` |
| Invoice PDF | `facturas/nota_{nota_id}` |
| Sucursal logo | `logos/sucursales/{sucursal_id}` |

## Datetime convention

Store **naive UTC** (`datetime.utcnow()` everywhere); render local via `datetime_local` / `date_local` Jinja filters (`app/core/datetime_utils.py`, `APP_TIMEZONE=America/Mexico_City`). Date-only fields (`Nota.fecha_caducidad_pago`, `CorteCaja.fecha`) are business days, never tz-converted. The only tz-correct day-boundary math is `_corte_local_day_bounds` (corte de caja windows); the contabilidad report's date filter is NOT tz-correct (see `KNOWN_ISSUES.md` #2).

## Migrations

- `alembic.ini` leaves `sqlalchemy.url` empty; `migrations/env.py` injects `settings.DATABASE_URL` (normalizing Heroku's `postgres://` → `postgresql://`).
- Chain is linear, single head. `Procfile` `release: alembic upgrade head` applies migrations automatically on every Heroku deploy.
- **`render_as_batch` is not configured** — hand-write ALTERs with `op.batch_alter_table(...)` so they work on SQLite (dev). Existing migrations show the pattern (e.g. `fb1c2d3e4f56`).
- Docstring `Revises:` comments in several migration files are stale — trust `down_revision`, not the docstring.
- Migration filenames double as a feature changelog: foundations (users/materials/precios/notas, Dec 2025) → cuentas/IVA/comisiones/corte de caja (Jan–Feb 2026) → sucursal scoping + reversible actions (Mar 2026) → visor role, caja categories, inventory valuation, cheques (Apr 2026).

## Deployment

- `git push heroku main` (remote `heroku` → app `scrap360`); GitHub remote `origin` → `blankhall98/Metaleria-API`.
- Release phase runs migrations; web dyno: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- Prod config via Heroku config vars: `DATABASE_URL` (auto), `SECRET_KEY`, `ENV=prod`, `FIREBASE_CREDENTIALS_JSON`, `SUPERADMIN_*` (used once by the seed script).
- Health probe: `GET /healthz` (unauthenticated).
